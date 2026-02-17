from datetime import datetime

from extensions import db


class ItamAsset(db.Model):
    __tablename__ = "itam_assets"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    canonical_key = db.Column(db.String(255), nullable=False, index=True)
    asset_name = db.Column(db.String(255), nullable=True, index=True)
    hostname = db.Column(db.String(255), nullable=True, index=True)
    asset_type = db.Column(db.String(64), nullable=False, default="unknown", index=True)
    platform = db.Column(db.String(64), nullable=True, index=True)

    primary_ip = db.Column(db.String(64), nullable=True, index=True)
    primary_mac = db.Column(db.String(64), nullable=True, index=True)
    serial_number = db.Column(db.String(128), nullable=True, index=True)

    vendor = db.Column(db.String(128), nullable=True, index=True)
    model = db.Column(db.String(128), nullable=True, index=True)
    os_name = db.Column(db.String(128), nullable=True, index=True)
    os_version = db.Column(db.String(128), nullable=True)
    domain = db.Column(db.String(128), nullable=True, index=True)
    location = db.Column(db.String(128), nullable=True, index=True)
    environment = db.Column(db.String(64), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)

    source_count = db.Column(db.Integer, nullable=False, default=0)
    tags_json = db.Column(db.JSON, nullable=False, default=list)
    custom_fields_json = db.Column(db.JSON, nullable=False, default=dict)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    first_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    identities = db.relationship(
        "ItamAssetIdentity",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sources = db.relationship(
        "ItamAssetSource",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    software = db.relationship(
        "ItamAssetSoftware",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    hardware = db.relationship(
        "ItamAssetHardware",
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    network_interfaces = db.relationship(
        "ItamAssetNetworkInterface",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tag_rows = db.relationship(
        "ItamAssetTag",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    lifecycle_rows = db.relationship(
        "ItamAssetLifecycle",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ItamAssetLifecycle.effective_at.desc()",
    )
    bindings = db.relationship(
        "ItamAssetItomBinding",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    compliance_findings = db.relationship(
        "ItamComplianceFinding",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ItamComplianceFinding.updated_at.desc()",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "canonical_key",
            name="uq_itam_asset_customer_canonical_key",
        ),
    )

    def to_dict(self, include_details=False):
        current_lifecycle = None
        for row in self.lifecycle_rows or []:
            if row.is_current:
                current_lifecycle = row
                break

        data = {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "canonical_key": self.canonical_key,
            "asset_name": self.asset_name or "",
            "hostname": self.hostname or "",
            "asset_type": self.asset_type,
            "platform": self.platform or "",
            "primary_ip": self.primary_ip or "",
            "primary_mac": self.primary_mac or "",
            "serial_number": self.serial_number or "",
            "vendor": self.vendor or "",
            "model": self.model or "",
            "os_name": self.os_name or "",
            "os_version": self.os_version or "",
            "domain": self.domain or "",
            "location": self.location or "",
            "environment": self.environment or "",
            "status": self.status,
            "source_count": self.source_count,
            "tags": self.tags_json or [],
            "custom_fields": self.custom_fields_json or {},
            "metadata": self.metadata_json or {},
            "lifecycle_stage": current_lifecycle.stage if current_lifecycle else "",
            "lifecycle_status": current_lifecycle.status if current_lifecycle else "",
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_discovered_at": (
                self.last_discovered_at.isoformat() if self.last_discovered_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_details:
            data["identities"] = [x.to_dict() for x in self.identities]
            data["sources"] = [x.to_dict() for x in self.sources]
            data["software"] = [x.to_dict() for x in self.software]
            data["hardware"] = self.hardware.to_dict() if self.hardware else None
            data["network_interfaces"] = [x.to_dict() for x in self.network_interfaces]
            data["tag_records"] = [x.to_dict() for x in self.tag_rows]
            data["lifecycle"] = current_lifecycle.to_dict() if current_lifecycle else None
            data["lifecycle_history"] = [x.to_dict() for x in self.lifecycle_rows]
            data["itom_bindings"] = [x.to_dict() for x in self.bindings]
            data["compliance_findings"] = [x.to_dict() for x in self.compliance_findings]
        return data


class ItamAssetIdentity(db.Model):
    __tablename__ = "itam_asset_identities"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identity_type = db.Column(db.String(64), nullable=False, index=True)
    identity_value = db.Column(db.String(255), nullable=False, index=True)
    confidence = db.Column(db.Integer, nullable=False, default=100)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="identities")

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "identity_type",
            "identity_value",
            name="uq_itam_identity_customer_type_value",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "identity_type": self.identity_type,
            "identity_value": self.identity_value,
            "confidence": self.confidence,
            "is_primary": self.is_primary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ItamAssetSource(db.Model):
    __tablename__ = "itam_asset_sources"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_name = db.Column(db.String(64), nullable=False, index=True)
    source_key = db.Column(db.String(255), nullable=False, index=True)
    confidence = db.Column(db.Integer, nullable=False, default=80)
    raw_json = db.Column(db.JSON, nullable=False, default=dict)
    discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="sources")

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "source_name",
            "source_key",
            name="uq_itam_source_customer_name_key",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "source_name": self.source_name,
            "source_key": self.source_key,
            "confidence": self.confidence,
            "raw": self.raw_json or {},
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetSoftware(db.Model):
    __tablename__ = "itam_asset_software"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False, index=True)
    version = db.Column(db.String(128), nullable=True, index=True)
    vendor = db.Column(db.String(128), nullable=True, index=True)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="software")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "name",
            "version",
            name="uq_itam_asset_software_name_version",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "name": self.name,
            "version": self.version or "",
            "vendor": self.vendor or "",
            "source_name": self.source_name or "",
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetHardware(db.Model):
    __tablename__ = "itam_asset_hardware"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )
    cpu_model = db.Column(db.String(255), nullable=True)
    cpu_cores = db.Column(db.Integer, nullable=True)
    memory_mb = db.Column(db.Integer, nullable=True)
    storage_mb = db.Column(db.Integer, nullable=True)
    bios_version = db.Column(db.String(128), nullable=True)
    firmware_version = db.Column(db.String(128), nullable=True)
    manufacturer = db.Column(db.String(128), nullable=True, index=True)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    captured_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="hardware")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "cpu_model": self.cpu_model or "",
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "storage_mb": self.storage_mb,
            "bios_version": self.bios_version or "",
            "firmware_version": self.firmware_version or "",
            "manufacturer": self.manufacturer or "",
            "source_name": self.source_name or "",
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetNetworkInterface(db.Model):
    __tablename__ = "itam_asset_network_interfaces"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interface_name = db.Column(db.String(128), nullable=True, index=True)
    mac_address = db.Column(db.String(64), nullable=True, index=True)
    ip_address = db.Column(db.String(64), nullable=True, index=True)
    subnet_mask = db.Column(db.String(64), nullable=True)
    gateway = db.Column(db.String(64), nullable=True)
    vlan = db.Column(db.String(64), nullable=True, index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="network_interfaces")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "interface_name",
            "mac_address",
            "ip_address",
            name="uq_itam_asset_network_interface_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "interface_name": self.interface_name or "",
            "mac_address": self.mac_address or "",
            "ip_address": self.ip_address or "",
            "subnet_mask": self.subnet_mask or "",
            "gateway": self.gateway or "",
            "vlan": self.vlan or "",
            "is_primary": bool(self.is_primary),
            "source_name": self.source_name or "",
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetTag(db.Model):
    __tablename__ = "itam_asset_tags"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_key = db.Column(db.String(128), nullable=False, default="label", index=True)
    tag_value = db.Column(db.String(255), nullable=False, index=True)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="tag_rows")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "tag_key",
            "tag_value",
            name="uq_itam_asset_tag_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "tag_key": self.tag_key,
            "tag_value": self.tag_value,
            "source_name": self.source_name or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetLifecycle(db.Model):
    __tablename__ = "itam_asset_lifecycle"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = db.Column(db.String(64), nullable=False, default="discovered", index=True)
    status = db.Column(db.String(64), nullable=False, default="active", index=True)
    owner = db.Column(db.String(128), nullable=True, index=True)
    cost_center = db.Column(db.String(128), nullable=True, index=True)
    warranty_end = db.Column(db.Date, nullable=True)
    eol_date = db.Column(db.Date, nullable=True)
    decommission_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    lifecycle_tags_json = db.Column(db.JSON, nullable=False, default=list)
    is_current = db.Column(db.Boolean, nullable=False, default=True, index=True)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    effective_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="lifecycle_rows")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "stage": self.stage,
            "status": self.status,
            "owner": self.owner or "",
            "cost_center": self.cost_center or "",
            "warranty_end": self.warranty_end.isoformat() if self.warranty_end else None,
            "eol_date": self.eol_date.isoformat() if self.eol_date else None,
            "decommission_date": (
                self.decommission_date.isoformat() if self.decommission_date else None
            ),
            "notes": self.notes or "",
            "lifecycle_tags": self.lifecycle_tags_json or [],
            "is_current": bool(self.is_current),
            "source_name": self.source_name or "",
            "effective_at": self.effective_at.isoformat() if self.effective_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetRelation(db.Model):
    __tablename__ = "itam_asset_relations"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type = db.Column(db.String(64), nullable=False, default="depends_on", index=True)
    confidence = db.Column(db.Integer, nullable=False, default=50)
    source_name = db.Column(db.String(64), nullable=True, index=True)
    discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "from_asset_id",
            "to_asset_id",
            "relation_type",
            name="uq_itam_asset_relation_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "from_asset_id": self.from_asset_id,
            "to_asset_id": self.to_asset_id,
            "relation_type": self.relation_type,
            "confidence": self.confidence,
            "source_name": self.source_name or "",
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ItamDiscoveryRun(db.Model):
    __tablename__ = "itam_discovery_runs"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_name = db.Column(db.String(128), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="running", index=True)
    stats_json = db.Column(db.JSON, nullable=False, default=dict)
    error_text = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "source_name": self.source_name,
            "status": self.status,
            "stats": self.stats_json or {},
            "error_text": self.error_text,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ItamDiscoveryPolicy(db.Model):
    __tablename__ = "itam_discovery_policies"

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False, index=True)
    interval_minutes = db.Column(db.Integer, nullable=False, default=60)
    sources_json = db.Column(db.JSON, nullable=False, default=list)
    target_customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    last_run_started_at = db.Column(db.DateTime, nullable=True)
    last_run_ended_at = db.Column(db.DateTime, nullable=True)
    last_run_status = db.Column(db.String(32), nullable=True, index=True)
    last_run_summary_json = db.Column(db.JSON, nullable=False, default=dict)
    last_error_text = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    target_customer = db.relationship("Customer", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "enabled": bool(self.enabled),
            "interval_minutes": int(self.interval_minutes or 0),
            "sources": self.sources_json or [],
            "target_customer_id": self.target_customer_id,
            "target_customer_name": (
                self.target_customer.name if self.target_customer else ""
            ),
            "last_run_started_at": (
                self.last_run_started_at.isoformat() if self.last_run_started_at else None
            ),
            "last_run_ended_at": (
                self.last_run_ended_at.isoformat() if self.last_run_ended_at else None
            ),
            "last_run_status": self.last_run_status or "",
            "last_run_summary": self.last_run_summary_json or {},
            "last_error_text": self.last_error_text or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamCloudIntegration(db.Model):
    __tablename__ = "itam_cloud_integrations"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider = db.Column(db.String(32), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    config_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "provider",
            "name",
            name="uq_itam_cloud_integration_customer_provider_name",
        ),
    )

    @staticmethod
    def _mask_config(cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        out = {}
        for k, v in cfg.items():
            key = str(k or "")
            low = key.lower()
            if any(
                x in low
                for x in (
                    "secret",
                    "password",
                    "token",
                    "private_key",
                    "access_key",
                    "credential",
                )
            ):
                if isinstance(v, str) and v:
                    out[key] = f"{v[:2]}***"
                else:
                    out[key] = "***"
            else:
                out[key] = v
        return out

    def to_dict(self, include_secrets=False):
        cfg = self.config_json or {}
        if not include_secrets:
            cfg = self._mask_config(cfg)
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "provider": self.provider,
            "name": self.name,
            "enabled": bool(self.enabled),
            "config": cfg,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamCompliancePolicy(db.Model):
    __tablename__ = "itam_compliance_policies"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    code = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(16), nullable=False, default="medium", index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    policy_type = db.Column(db.String(64), nullable=False, default="required_tag", index=True)
    criteria_json = db.Column(db.JSON, nullable=False, default=dict)
    target_filters_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    findings = db.relationship(
        "ItamComplianceFinding",
        back_populates="policy",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "code",
            name="uq_itam_compliance_policy_customer_code",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "code": self.code,
            "name": self.name,
            "description": self.description or "",
            "severity": self.severity,
            "enabled": bool(self.enabled),
            "policy_type": self.policy_type,
            "criteria": self.criteria_json or {},
            "target_filters": self.target_filters_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamComplianceRun(db.Model):
    __tablename__ = "itam_compliance_runs"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, default="running", index=True)
    triggered_by = db.Column(db.String(128), nullable=True, index=True)
    policy_count = db.Column(db.Integer, nullable=False, default=0)
    asset_count = db.Column(db.Integer, nullable=False, default=0)
    finding_count = db.Column(db.Integer, nullable=False, default=0)
    pass_count = db.Column(db.Integer, nullable=False, default=0)
    fail_count = db.Column(db.Integer, nullable=False, default=0)
    not_applicable_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    summary_json = db.Column(db.JSON, nullable=False, default=dict)
    error_text = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    customer = db.relationship("Customer", lazy="joined")
    findings = db.relationship(
        "ItamComplianceFinding",
        back_populates="run",
        passive_deletes=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "status": self.status,
            "triggered_by": self.triggered_by or "",
            "policy_count": int(self.policy_count or 0),
            "asset_count": int(self.asset_count or 0),
            "finding_count": int(self.finding_count or 0),
            "pass_count": int(self.pass_count or 0),
            "fail_count": int(self.fail_count or 0),
            "not_applicable_count": int(self.not_applicable_count or 0),
            "error_count": int(self.error_count or 0),
            "summary": self.summary_json or {},
            "error_text": self.error_text or "",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ItamComplianceFinding(db.Model):
    __tablename__ = "itam_compliance_findings"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_compliance_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_compliance_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(24), nullable=False, default="not_applicable", index=True)
    score = db.Column(db.Integer, nullable=False, default=0)
    details_json = db.Column(db.JSON, nullable=False, default=dict)
    evaluated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    run = db.relationship("ItamComplianceRun", back_populates="findings")
    policy = db.relationship("ItamCompliancePolicy", back_populates="findings")
    asset = db.relationship("ItamAsset", back_populates="compliance_findings")

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "policy_id",
            "asset_id",
            name="uq_itam_compliance_policy_asset",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "run_id": self.run_id,
            "policy_id": self.policy_id,
            "policy_name": self.policy.name if self.policy else "",
            "policy_code": self.policy.code if self.policy else "",
            "asset_id": self.asset_id,
            "asset_name": (
                self.asset.asset_name or self.asset.hostname or self.asset.canonical_key
                if self.asset
                else ""
            ),
            "status": self.status,
            "score": int(self.score or 0),
            "details": self.details_json or {},
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ItamAssetItomBinding(db.Model):
    __tablename__ = "itam_asset_itom_bindings"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("itam_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("business_applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    service_id = db.Column(
        db.Integer,
        db.ForeignKey("application_services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    relation_type = db.Column(db.String(32), nullable=False, default="supports", index=True)
    confidence = db.Column(db.Integer, nullable=False, default=70)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    asset = db.relationship("ItamAsset", back_populates="bindings")
    application = db.relationship("BusinessApplication", lazy="joined")
    service = db.relationship("ApplicationService", lazy="joined")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "application_id",
            "service_id",
            name="uq_itam_asset_itom_binding_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "asset_id": self.asset_id,
            "application_id": self.application_id,
            "application_name": self.application.name if self.application else "",
            "service_id": self.service_id,
            "service_name": self.service.name if self.service else "",
            "relation_type": self.relation_type,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
