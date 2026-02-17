import re
import uuid
from datetime import date, datetime, timezone

from extensions import db
from models.itam import (
    ItamAsset,
    ItamAssetHardware,
    ItamAssetIdentity,
    ItamAssetItomBinding,
    ItamAssetLifecycle,
    ItamAssetNetworkInterface,
    ItamAssetRelation,
    ItamAssetSoftware,
    ItamAssetSource,
    ItamAssetTag,
)
from services.itam.normalize import (
    classify_asset,
    maybe_ip_from_text,
    norm_hostname,
    norm_ip,
    norm_lower,
    norm_mac,
    norm_str,
)


_IDENTITY_WEIGHTS = {
    "agent_id": 100,
    "cloud_instance_id": 99,
    "serial_number": 98,
    "device_uuid": 97,
    "mac": 95,
    "ip": 86,
    "hostname": 82,
}


def _non_empty(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _normalize_status(value):
    s = norm_lower(value)
    if not s:
        return "active"
    if s in ("up", "healthy", "active", "online", "running"):
        return "active"
    if s in ("down", "inactive", "offline", "unknown"):
        return "inactive"
    return s


def _to_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"-?\d+", norm_str(value).replace(",", ""))
    if not m:
        return None
    return int(m.group(0))


def _to_mb(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)

    s = norm_lower(value).replace(",", "")
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    n = float(m.group(1))

    if "tb" in s or "tib" in s:
        n *= 1024 * 1024
    elif "gb" in s or "gib" in s:
        n *= 1024
    elif "kb" in s or "kib" in s:
        n /= 1024
    elif re.search(r"\bbytes?\b", s):
        n /= (1024 * 1024)
    return int(n)


def _to_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = norm_str(value)
    if not s:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _identity_candidates(record):
    seen = set()
    out = []

    def add(identity_type, value, confidence=90, is_primary=False):
        v = norm_str(value)
        if not v:
            return
        key = (identity_type, v)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "identity_type": identity_type,
                "identity_value": v,
                "confidence": int(confidence),
                "is_primary": bool(is_primary),
            }
        )

    agent_id = norm_str(record.get("agent_id"))
    cloud_id = norm_str(record.get("cloud_instance_id"))
    serial = norm_str(record.get("serial_number"))
    device_uuid = norm_str(record.get("device_uuid"))
    host = norm_hostname(record.get("hostname") or record.get("asset_name"))
    ip = norm_ip(record.get("primary_ip")) or maybe_ip_from_text(record.get("asset_name"))
    mac = norm_mac(record.get("primary_mac"))

    add("agent_id", agent_id, confidence=99, is_primary=True)
    add("cloud_instance_id", cloud_id, confidence=98, is_primary=not agent_id)
    add("serial_number", serial, confidence=97, is_primary=not agent_id and not cloud_id)
    add(
        "device_uuid",
        device_uuid,
        confidence=97,
        is_primary=not agent_id and not cloud_id and not serial,
    )
    add(
        "mac",
        mac,
        confidence=95,
        is_primary=not agent_id and not cloud_id and not serial and not device_uuid,
    )
    add(
        "ip",
        ip,
        confidence=88,
        is_primary=not agent_id and not cloud_id and not serial and not device_uuid and not mac,
    )
    add(
        "hostname",
        host,
        confidence=85,
        is_primary=(
            not agent_id
            and not cloud_id
            and not serial
            and not device_uuid
            and not mac
            and not ip
        ),
    )
    return out


def _find_assets_by_identity(customer_id, identities):
    if not identities:
        return []

    filters = []
    incoming = {}
    for x in identities:
        filters.append(
            db.and_(
                ItamAssetIdentity.identity_type == x["identity_type"],
                ItamAssetIdentity.identity_value == x["identity_value"],
            )
        )
        incoming[(x["identity_type"], x["identity_value"])] = int(x.get("confidence") or 80)

    rows = (
        ItamAssetIdentity.query.filter(ItamAssetIdentity.customer_id == customer_id)
        .filter(db.or_(*filters))
        .all()
    )
    if not rows:
        return []

    scores = {}
    for row in rows:
        key = (row.identity_type, row.identity_value)
        entry = scores.setdefault(row.asset_id, {"score": 0, "hits": 0})
        incoming_conf = incoming.get(key, 80)
        weight = _IDENTITY_WEIGHTS.get(row.identity_type, 60)
        entry["score"] += int((weight + int(row.confidence or 80) + incoming_conf) / 3)
        entry["hits"] += 1

    assets = ItamAsset.query.filter(ItamAsset.id.in_(list(scores.keys()))).all()
    assets.sort(
        key=lambda a: (scores[a.id]["score"], scores[a.id]["hits"], a.last_seen or datetime.min),
        reverse=True,
    )
    return assets


def _record_strength(identities, source_confidence):
    src = max(1, min(int(source_confidence or 80), 100))
    if not identities:
        return src
    top = sorted([int(x.get("confidence") or 80) for x in identities], reverse=True)[:2]
    ids = int(sum(top) / len(top))
    return max(1, min(int((ids * 0.6) + (src * 0.4)), 100))


def _upsert_source(asset, customer_id, source_name, source_key, record, discovered_at, confidence):
    source = ItamAssetSource.query.filter(
        ItamAssetSource.customer_id == customer_id,
        ItamAssetSource.source_name == source_name,
        ItamAssetSource.source_key == source_key,
    ).first()

    created = False
    if not source:
        source = ItamAssetSource(
            customer_id=customer_id,
            asset_id=asset.id,
            source_name=source_name,
            source_key=source_key,
        )
        db.session.add(source)
        created = True

    source.asset_id = asset.id
    source.raw_json = record
    source.confidence = int(confidence or 80)
    source.discovered_at = discovered_at
    source.updated_at = datetime.now(timezone.utc)
    return created


def _upsert_identities(asset, customer_id, identities):
    for idx, ident in enumerate(identities):
        row = ItamAssetIdentity.query.filter(
            ItamAssetIdentity.customer_id == customer_id,
            ItamAssetIdentity.identity_type == ident["identity_type"],
            ItamAssetIdentity.identity_value == ident["identity_value"],
        ).first()
        if not row:
            row = ItamAssetIdentity(
                customer_id=customer_id,
                asset_id=asset.id,
                identity_type=ident["identity_type"],
                identity_value=ident["identity_value"],
            )
            db.session.add(row)
        row.asset_id = asset.id
        row.confidence = int(ident.get("confidence") or 90)
        row.is_primary = bool(ident.get("is_primary") and idx == 0)


def _upsert_software(asset, customer_id, software_list, source_name, discovered_at):
    for sw in software_list or []:
        if isinstance(sw, str):
            sw_name = norm_str(sw)
            sw_ver = ""
            sw_vendor = ""
        elif isinstance(sw, dict):
            sw_name = norm_str(sw.get("name"))
            sw_ver = norm_str(sw.get("version"))
            sw_vendor = norm_str(sw.get("vendor"))
        else:
            continue

        if not sw_name:
            continue

        row = ItamAssetSoftware.query.filter(
            ItamAssetSoftware.asset_id == asset.id,
            ItamAssetSoftware.name == sw_name,
            ItamAssetSoftware.version == sw_ver,
        ).first()
        if not row:
            row = ItamAssetSoftware(
                customer_id=customer_id,
                asset_id=asset.id,
                name=sw_name,
                version=sw_ver,
            )
            db.session.add(row)

        row.vendor = sw_vendor
        row.source_name = source_name
        row.discovered_at = discovered_at
        row.updated_at = datetime.now(timezone.utc)


def _parse_tag_entries(tags):
    out = []
    seen = set()
    for tag in tags or []:
        key = "label"
        value = ""
        if isinstance(tag, str):
            value = norm_str(tag)
        elif isinstance(tag, dict):
            key = norm_lower(tag.get("key") or tag.get("name") or "label") or "label"
            value = norm_str(tag.get("value") or tag.get("tag"))
            if not value and key != "label":
                value = key
                key = "label"
        if not value:
            continue
        mark = (key, value)
        if mark in seen:
            continue
        seen.add(mark)
        out.append({"tag_key": key, "tag_value": value})
    return out


def _sync_tag_rows(asset, customer_id, tag_entries, source_name):
    now = datetime.now(timezone.utc)
    existing_rows = ItamAssetTag.query.filter(ItamAssetTag.asset_id == asset.id).all()
    existing = {(x.tag_key, x.tag_value): x for x in existing_rows}
    values = {x.tag_value for x in existing_rows if x.tag_value}

    for item in tag_entries:
        key = norm_lower(item.get("tag_key") or "label") or "label"
        value = norm_str(item.get("tag_value"))
        if not value:
            continue
        values.add(value)
        row = existing.get((key, value))
        if not row:
            row = ItamAssetTag(
                customer_id=customer_id,
                asset_id=asset.id,
                tag_key=key,
                tag_value=value,
            )
            db.session.add(row)
        row.source_name = source_name
        row.updated_at = now

    if values:
        asset.tags_json = sorted(values)


def _upsert_hardware(asset, customer_id, record, source_name, discovered_at):
    hw = record.get("hardware") if isinstance(record.get("hardware"), dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}

    updates = {
        "cpu_model": norm_str(hw.get("cpu_model") or metadata.get("cpu")),
        "cpu_cores": _to_int(hw.get("cpu_cores") or metadata.get("cpu_cores")),
        "memory_mb": _to_mb(hw.get("memory_mb") or hw.get("memory") or metadata.get("mem")),
        "storage_mb": _to_mb(hw.get("storage_mb") or hw.get("storage") or metadata.get("disk")),
        "bios_version": norm_str(hw.get("bios_version") or metadata.get("bios_version")),
        "firmware_version": norm_str(hw.get("firmware_version") or metadata.get("firmware_version")),
        "manufacturer": norm_str(hw.get("manufacturer") or record.get("vendor")),
    }
    if not any(_non_empty(v) for v in updates.values()):
        return

    row = ItamAssetHardware.query.filter(ItamAssetHardware.asset_id == asset.id).first()
    if not row:
        row = ItamAssetHardware(customer_id=customer_id, asset_id=asset.id)
        db.session.add(row)

    for field, value in updates.items():
        if value is None:
            continue
        if _non_empty(value):
            setattr(row, field, value)

    row.source_name = source_name
    row.captured_at = discovered_at
    row.updated_at = datetime.now(timezone.utc)


def _extract_network_interfaces(record):
    out = []
    rows = record.get("network_interfaces")
    if isinstance(rows, list):
        for idx, raw in enumerate(rows):
            if not isinstance(raw, dict):
                continue
            name = norm_str(raw.get("name") or raw.get("interface") or raw.get("ifname"))
            mac = norm_mac(raw.get("mac") or raw.get("mac_address"))
            ip = norm_ip(raw.get("ip") or raw.get("ip_address"))
            if not (name or mac or ip):
                continue
            out.append(
                {
                    "interface_name": name or f"if{idx}",
                    "mac_address": mac,
                    "ip_address": ip,
                    "subnet_mask": norm_str(raw.get("subnet_mask")),
                    "gateway": norm_str(raw.get("gateway")),
                    "vlan": norm_str(raw.get("vlan")),
                    "is_primary": bool(raw.get("is_primary")),
                }
            )

    if not out:
        mac = norm_mac(record.get("primary_mac"))
        ip = norm_ip(record.get("primary_ip"))
        if mac or ip:
            out.append(
                {
                    "interface_name": "primary",
                    "mac_address": mac,
                    "ip_address": ip,
                    "subnet_mask": "",
                    "gateway": "",
                    "vlan": "",
                    "is_primary": True,
                }
            )

    seen = set()
    unique = []
    for row in out:
        key = (row["interface_name"], row["mac_address"], row["ip_address"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _upsert_network_interfaces(asset, customer_id, record, source_name, discovered_at):
    rows = _extract_network_interfaces(record)
    if not rows:
        return

    now = datetime.now(timezone.utc)
    for idx, iface in enumerate(rows):
        row = ItamAssetNetworkInterface.query.filter(
            ItamAssetNetworkInterface.asset_id == asset.id,
            ItamAssetNetworkInterface.interface_name == iface["interface_name"],
            ItamAssetNetworkInterface.mac_address == iface["mac_address"],
            ItamAssetNetworkInterface.ip_address == iface["ip_address"],
        ).first()
        if not row:
            row = ItamAssetNetworkInterface(
                customer_id=customer_id,
                asset_id=asset.id,
                interface_name=iface["interface_name"],
                mac_address=iface["mac_address"],
                ip_address=iface["ip_address"],
            )
            db.session.add(row)

        for field in ("subnet_mask", "gateway", "vlan"):
            value = iface.get(field)
            if _non_empty(value):
                setattr(row, field, value)

        row.is_primary = bool(iface.get("is_primary")) or idx == 0
        row.source_name = source_name
        row.discovered_at = discovered_at
        row.updated_at = now


def _upsert_lifecycle_from_record(asset, customer_id, record, source_name, discovered_at):
    payload = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else None
    if not payload:
        return

    stage = norm_lower(payload.get("stage") or payload.get("phase") or "discovered")
    status = _normalize_status(payload.get("status") or asset.status or "active")
    owner = norm_str(payload.get("owner"))
    cost_center = norm_str(payload.get("cost_center"))
    warranty_end = _to_date(payload.get("warranty_end"))
    eol_date = _to_date(payload.get("eol_date"))
    decommission_date = _to_date(payload.get("decommission_date"))
    notes = norm_str(payload.get("notes"))
    lifecycle_tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    lifecycle_tags = sorted({norm_str(x) for x in lifecycle_tags if norm_str(x)})

    current = ItamAssetLifecycle.query.filter(
        ItamAssetLifecycle.asset_id == asset.id,
        ItamAssetLifecycle.is_current.is_(True),
    ).first()

    if current:
        same = (
            current.stage == stage
            and current.status == status
            and (current.owner or "") == owner
            and (current.cost_center or "") == cost_center
            and current.warranty_end == warranty_end
            and current.eol_date == eol_date
            and current.decommission_date == decommission_date
            and (current.notes or "") == notes
            and sorted(current.lifecycle_tags_json or []) == lifecycle_tags
        )
        if same:
            current.source_name = source_name
            current.effective_at = discovered_at
            current.updated_at = datetime.now(timezone.utc)
            return
        current.is_current = False

    row = ItamAssetLifecycle(
        customer_id=customer_id,
        asset_id=asset.id,
        stage=stage,
        status=status,
        owner=owner,
        cost_center=cost_center,
        warranty_end=warranty_end,
        eol_date=eol_date,
        decommission_date=decommission_date,
        notes=notes,
        lifecycle_tags_json=lifecycle_tags,
        is_current=True,
        source_name=source_name,
        effective_at=discovered_at,
    )
    db.session.add(row)


def _merge_duplicate_assets(primary_asset, duplicate_assets):
    if not primary_asset or not duplicate_assets:
        return primary_asset

    now = datetime.now(timezone.utc)
    for duplicate in duplicate_assets:
        if not duplicate or duplicate.id == primary_asset.id:
            continue

        for field in (
            "asset_name",
            "hostname",
            "asset_type",
            "platform",
            "primary_ip",
            "primary_mac",
            "serial_number",
            "vendor",
            "model",
            "os_name",
            "os_version",
            "domain",
            "location",
            "environment",
            "status",
        ):
            current = getattr(primary_asset, field)
            incoming = getattr(duplicate, field)
            if (not _non_empty(current)) and _non_empty(incoming):
                setattr(primary_asset, field, incoming)

        primary_tags = {norm_str(x) for x in (primary_asset.tags_json or []) if norm_str(x)}
        duplicate_tags = {norm_str(x) for x in (duplicate.tags_json or []) if norm_str(x)}
        primary_asset.tags_json = sorted(primary_tags.union(duplicate_tags))

        merged_custom = dict(duplicate.custom_fields_json or {})
        merged_custom.update(primary_asset.custom_fields_json or {})
        primary_asset.custom_fields_json = merged_custom

        merged_meta = dict(duplicate.metadata_json or {})
        merged_meta.update(primary_asset.metadata_json or {})
        primary_asset.metadata_json = merged_meta

        if duplicate.first_seen and (
            not primary_asset.first_seen or duplicate.first_seen < primary_asset.first_seen
        ):
            primary_asset.first_seen = duplicate.first_seen
        if duplicate.last_seen and (
            not primary_asset.last_seen or duplicate.last_seen > primary_asset.last_seen
        ):
            primary_asset.last_seen = duplicate.last_seen
        if duplicate.last_discovered_at and (
            not primary_asset.last_discovered_at
            or duplicate.last_discovered_at > primary_asset.last_discovered_at
        ):
            primary_asset.last_discovered_at = duplicate.last_discovered_at

        duplicate_identities = ItamAssetIdentity.query.filter(
            ItamAssetIdentity.asset_id == duplicate.id
        ).all()
        for row in duplicate_identities:
            existing = ItamAssetIdentity.query.filter(
                ItamAssetIdentity.asset_id == primary_asset.id,
                ItamAssetIdentity.identity_type == row.identity_type,
                ItamAssetIdentity.identity_value == row.identity_value,
            ).first()
            if existing and existing.id != row.id:
                existing.confidence = max(int(existing.confidence or 0), int(row.confidence or 0))
                existing.is_primary = bool(existing.is_primary or row.is_primary)
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_sources = ItamAssetSource.query.filter(ItamAssetSource.asset_id == duplicate.id).all()
        for row in duplicate_sources:
            existing = ItamAssetSource.query.filter(
                ItamAssetSource.asset_id == primary_asset.id,
                ItamAssetSource.source_name == row.source_name,
                ItamAssetSource.source_key == row.source_key,
            ).first()
            if existing and existing.id != row.id:
                existing.confidence = max(int(existing.confidence or 0), int(row.confidence or 0))
                if row.discovered_at and (
                    not existing.discovered_at or row.discovered_at > existing.discovered_at
                ):
                    existing.discovered_at = row.discovered_at
                    existing.raw_json = row.raw_json
                existing.updated_at = now
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_software = ItamAssetSoftware.query.filter(
            ItamAssetSoftware.asset_id == duplicate.id
        ).all()
        for row in duplicate_software:
            existing = ItamAssetSoftware.query.filter(
                ItamAssetSoftware.asset_id == primary_asset.id,
                ItamAssetSoftware.name == row.name,
                ItamAssetSoftware.version == row.version,
            ).first()
            if existing and existing.id != row.id:
                if _non_empty(row.vendor) and not _non_empty(existing.vendor):
                    existing.vendor = row.vendor
                if row.discovered_at and (
                    not existing.discovered_at or row.discovered_at > existing.discovered_at
                ):
                    existing.discovered_at = row.discovered_at
                    existing.source_name = row.source_name
                existing.updated_at = now
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_hardware = ItamAssetHardware.query.filter(ItamAssetHardware.asset_id == duplicate.id).first()
        primary_hardware = ItamAssetHardware.query.filter(ItamAssetHardware.asset_id == primary_asset.id).first()
        if duplicate_hardware and not primary_hardware:
            duplicate_hardware.asset_id = primary_asset.id
        elif duplicate_hardware and primary_hardware:
            for field in (
                "cpu_model",
                "cpu_cores",
                "memory_mb",
                "storage_mb",
                "bios_version",
                "firmware_version",
                "manufacturer",
            ):
                current = getattr(primary_hardware, field)
                incoming = getattr(duplicate_hardware, field)
                if (current is None or current == "") and (incoming is not None and incoming != ""):
                    setattr(primary_hardware, field, incoming)
            primary_hardware.updated_at = now
            db.session.delete(duplicate_hardware)

        duplicate_nics = ItamAssetNetworkInterface.query.filter(
            ItamAssetNetworkInterface.asset_id == duplicate.id
        ).all()
        for row in duplicate_nics:
            existing = ItamAssetNetworkInterface.query.filter(
                ItamAssetNetworkInterface.asset_id == primary_asset.id,
                ItamAssetNetworkInterface.interface_name == row.interface_name,
                ItamAssetNetworkInterface.mac_address == row.mac_address,
                ItamAssetNetworkInterface.ip_address == row.ip_address,
            ).first()
            if existing and existing.id != row.id:
                existing.is_primary = bool(existing.is_primary or row.is_primary)
                if _non_empty(row.subnet_mask) and not _non_empty(existing.subnet_mask):
                    existing.subnet_mask = row.subnet_mask
                if _non_empty(row.gateway) and not _non_empty(existing.gateway):
                    existing.gateway = row.gateway
                if _non_empty(row.vlan) and not _non_empty(existing.vlan):
                    existing.vlan = row.vlan
                existing.updated_at = now
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_tags = ItamAssetTag.query.filter(ItamAssetTag.asset_id == duplicate.id).all()
        for row in duplicate_tags:
            existing = ItamAssetTag.query.filter(
                ItamAssetTag.asset_id == primary_asset.id,
                ItamAssetTag.tag_key == row.tag_key,
                ItamAssetTag.tag_value == row.tag_value,
            ).first()
            if existing and existing.id != row.id:
                existing.updated_at = now
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_lifecycle = ItamAssetLifecycle.query.filter(
            ItamAssetLifecycle.asset_id == duplicate.id
        ).all()
        for row in duplicate_lifecycle:
            row.asset_id = primary_asset.id

        duplicate_bindings = ItamAssetItomBinding.query.filter(
            ItamAssetItomBinding.asset_id == duplicate.id
        ).all()
        for row in duplicate_bindings:
            existing = ItamAssetItomBinding.query.filter(
                ItamAssetItomBinding.asset_id == primary_asset.id,
                ItamAssetItomBinding.application_id == row.application_id,
                ItamAssetItomBinding.service_id == row.service_id,
            ).first()
            if existing and existing.id != row.id:
                existing.confidence = max(int(existing.confidence or 0), int(row.confidence or 0))
                if not _non_empty(existing.relation_type) and _non_empty(row.relation_type):
                    existing.relation_type = row.relation_type
                existing.updated_at = now
                db.session.delete(row)
            else:
                row.asset_id = primary_asset.id

        duplicate_relations = ItamAssetRelation.query.filter(
            db.or_(
                ItamAssetRelation.from_asset_id == duplicate.id,
                ItamAssetRelation.to_asset_id == duplicate.id,
            )
        ).all()
        for row in duplicate_relations:
            new_from = primary_asset.id if row.from_asset_id == duplicate.id else row.from_asset_id
            new_to = primary_asset.id if row.to_asset_id == duplicate.id else row.to_asset_id
            if new_from == new_to:
                db.session.delete(row)
                continue

            existing = ItamAssetRelation.query.filter(
                ItamAssetRelation.customer_id == row.customer_id,
                ItamAssetRelation.from_asset_id == new_from,
                ItamAssetRelation.to_asset_id == new_to,
                ItamAssetRelation.relation_type == row.relation_type,
            ).first()
            if existing and existing.id != row.id:
                existing.confidence = max(int(existing.confidence or 0), int(row.confidence or 0))
                if row.discovered_at and (
                    not existing.discovered_at or row.discovered_at > existing.discovered_at
                ):
                    existing.discovered_at = row.discovered_at
                db.session.delete(row)
            else:
                row.from_asset_id = new_from
                row.to_asset_id = new_to

        db.session.flush()
        db.session.delete(duplicate)

    lifecycle_rows = (
        ItamAssetLifecycle.query.filter(ItamAssetLifecycle.asset_id == primary_asset.id)
        .order_by(ItamAssetLifecycle.effective_at.desc(), ItamAssetLifecycle.id.desc())
        .all()
    )
    if lifecycle_rows:
        latest_id = lifecycle_rows[0].id
        for row in lifecycle_rows:
            row.is_current = row.id == latest_id

    primary_asset.source_count = ItamAssetSource.query.filter(
        ItamAssetSource.asset_id == primary_asset.id
    ).count()
    primary_asset.updated_at = now
    return primary_asset


def upsert_asset_from_record(customer_id, source_name, source_key, record, discovered_at, confidence=80):
    discovered_at = discovered_at or datetime.now(timezone.utc)
    identities = _identity_candidates(record)
    matches = _find_assets_by_identity(customer_id, identities)
    asset = matches[0] if matches else None
    if asset and len(matches) > 1:
        asset = _merge_duplicate_assets(asset, matches[1:])

    created = False
    if not asset:
        fallback_key = f"asset:{uuid.uuid4().hex}"
        if identities:
            first = identities[0]
            fallback_key = f"{first['identity_type']}:{first['identity_value']}"
        asset = ItamAsset(
            customer_id=customer_id,
            canonical_key=fallback_key[:255],
            first_seen=discovered_at,
        )
        db.session.add(asset)
        db.session.flush()
        created = True

    incoming_strength = _record_strength(identities, confidence)
    updates = {
        "asset_name": norm_str(record.get("asset_name")),
        "hostname": norm_hostname(record.get("hostname") or record.get("asset_name")),
        "asset_type": classify_asset(record),
        "platform": norm_str(record.get("platform")),
        "primary_ip": norm_ip(record.get("primary_ip")) or maybe_ip_from_text(record.get("asset_name")),
        "primary_mac": norm_mac(record.get("primary_mac")),
        "serial_number": norm_str(record.get("serial_number")),
        "vendor": norm_str(record.get("vendor")),
        "model": norm_str(record.get("model")),
        "os_name": norm_str(record.get("os_name") or record.get("os")),
        "os_version": norm_str(record.get("os_version")),
        "domain": norm_str(record.get("domain")),
        "location": norm_str(record.get("location")),
        "environment": norm_str(record.get("environment")),
        "status": _normalize_status(record.get("status")),
    }

    metadata = dict(asset.metadata_json or {})
    incoming_metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    metadata.update(incoming_metadata)
    field_conf = dict(metadata.get("_field_confidence") or {})

    for field, value in updates.items():
        if not _non_empty(value):
            continue
        current = getattr(asset, field)
        existing_conf = int(field_conf.get(field) or 0)
        should_update = (not _non_empty(current)) or (incoming_strength >= existing_conf)
        if field == "status":
            should_update = (not _non_empty(current)) or (incoming_strength + 5 >= existing_conf)
        if should_update:
            setattr(asset, field, value)
            field_conf[field] = incoming_strength

    metadata["_field_confidence"] = field_conf
    metadata["golden_record_strength"] = incoming_strength
    metadata["last_source"] = source_name
    asset.metadata_json = metadata

    custom_fields = record.get("custom_fields") if isinstance(record.get("custom_fields"), dict) else {}
    if custom_fields:
        merged_custom = dict(asset.custom_fields_json or {})
        merged_custom.update(custom_fields)
        asset.custom_fields_json = merged_custom

    tag_entries = _parse_tag_entries(record.get("tags") if isinstance(record.get("tags"), list) else [])
    if not tag_entries and asset.tags_json:
        tag_entries = _parse_tag_entries(asset.tags_json)
    if tag_entries:
        _sync_tag_rows(asset, customer_id, tag_entries, source_name)

    asset.last_seen = discovered_at
    asset.last_discovered_at = discovered_at
    asset.updated_at = datetime.now(timezone.utc)

    source_created = _upsert_source(
        asset=asset,
        customer_id=customer_id,
        source_name=source_name,
        source_key=source_key,
        record=record,
        discovered_at=discovered_at,
        confidence=confidence,
    )
    _upsert_identities(asset, customer_id, identities)
    _upsert_software(
        asset,
        customer_id,
        record.get("software") if isinstance(record.get("software"), list) else [],
        source_name=source_name,
        discovered_at=discovered_at,
    )
    _upsert_hardware(asset, customer_id, record, source_name, discovered_at)
    _upsert_network_interfaces(asset, customer_id, record, source_name, discovered_at)
    _upsert_lifecycle_from_record(asset, customer_id, record, source_name, discovered_at)

    if source_created:
        asset.source_count = int(asset.source_count or 0) + 1
    else:
        asset.source_count = ItamAssetSource.query.filter(
            ItamAssetSource.asset_id == asset.id
        ).count()

    return asset, created
