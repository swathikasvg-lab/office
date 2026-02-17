import os
import json
import socket
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from models.customer import Customer
from models.itam import ItamCloudIntegration
from models.snmp import SnmpConfig
from services.itam.normalize import maybe_ip_from_text, norm_hostname, norm_ip, norm_str


def _dt_from_epoch(value):
    try:
        ts = float(value or 0)
        if ts <= 0:
            return datetime.now(timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _servers_cache_path():
    return os.environ.get(
        "AUTOINTER_CACHE_DB",
        "/usr/local/autointelli/opsduty-server/.servers_cache.db",
    )


def _desktop_cache_path():
    return os.environ.get(
        "AUTOINTER_DESKTOP_CACHE_DB",
        "/usr/local/autointelli/opsduty-server/.desktops_cache.db",
    )


def _customer_name_map():
    rows = Customer.query.all()
    return {norm_str(c.name).lower(): c.cid for c in rows if c and c.name}


def _int_or_none(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _unique_items(items):
    out = []
    seen = set()
    for x in items or []:
        v = norm_str(x)
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _split_csv_or_list(value):
    if isinstance(value, list):
        return _unique_items(value)
    if isinstance(value, str):
        return _unique_items([x.strip() for x in value.split(",") if x.strip()])
    return []


def _cloud_integrations_from_db(provider, customer_id=None):
    try:
        query = ItamCloudIntegration.query.filter(
            ItamCloudIntegration.provider == norm_str(provider).lower(),
            ItamCloudIntegration.enabled.is_(True),
        )
        if customer_id is not None:
            query = query.filter(ItamCloudIntegration.customer_id == customer_id)
        rows = query.all()
    except Exception:
        return []

    out = []
    for row in rows:
        cfg = row.config_json if isinstance(row.config_json, dict) else {}
        item = dict(cfg)
        if "customer_id" not in item:
            item["customer_id"] = row.customer_id if row.customer_id is not None else customer_id
        item["integration_name"] = row.name
        out.append(item)
    return out


def _aws_default_regions():
    raw = os.environ.get("ITAM_AWS_REGIONS", "us-east-1")
    return _unique_items([x.strip() for x in raw.split(",") if x.strip()]) or ["us-east-1"]


def _aws_accounts_from_integrations(customer_id=None):
    rows = _cloud_integrations_from_db("aws", customer_id=customer_id)
    if not rows:
        return []

    default_regions = _aws_default_regions()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "customer_id": customer_id
                if customer_id is not None
                else _int_or_none(row.get("customer_id")),
                "account_id": norm_str(row.get("account_id")),
                "role_arn": norm_str(row.get("role_arn")),
                "external_id": norm_str(row.get("external_id")),
                "profile": norm_str(row.get("profile")),
                "regions": _split_csv_or_list(row.get("regions")) or default_regions,
                "integration_name": norm_str(row.get("integration_name")),
            }
        )
    return out


def _aws_accounts_from_env(customer_id=None):
    raw_json = norm_str(os.environ.get("ITAM_AWS_ACCOUNTS_JSON"))
    default_regions = _aws_default_regions()
    out = []

    if raw_json:
        try:
            parsed = json.loads(raw_json)
            rows = parsed if isinstance(parsed, list) else [parsed]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                regions = row.get("regions")
                if isinstance(regions, str):
                    regions = [x.strip() for x in regions.split(",") if x.strip()]
                elif not isinstance(regions, list):
                    regions = default_regions
                cid = customer_id if customer_id is not None else _int_or_none(row.get("customer_id"))
                out.append(
                    {
                        "customer_id": cid,
                        "account_id": norm_str(row.get("account_id")),
                        "role_arn": norm_str(row.get("role_arn")),
                        "external_id": norm_str(row.get("external_id")),
                        "profile": norm_str(row.get("profile")),
                        "regions": _unique_items(regions) or default_regions,
                    }
                )
        except Exception:
            out = []

    if out:
        return out

    env_customer = _int_or_none(os.environ.get("ITAM_AWS_CUSTOMER_ID"))
    out.append(
        {
            "customer_id": customer_id if customer_id is not None else env_customer,
            "account_id": norm_str(os.environ.get("ITAM_AWS_ACCOUNT_ID")),
            "role_arn": norm_str(os.environ.get("ITAM_AWS_ROLE_ARN")),
            "external_id": norm_str(os.environ.get("ITAM_AWS_EXTERNAL_ID")),
            "profile": norm_str(os.environ.get("ITAM_AWS_PROFILE")),
            "regions": default_regions,
        }
    )
    return out


def _make_aws_session(profile_name="", role_arn="", external_id="", region_name="us-east-1"):
    try:
        import boto3  # type: ignore
    except Exception:
        return None

    try:
        base = boto3.Session(profile_name=profile_name or None)
    except Exception:
        base = boto3.Session()

    if not role_arn:
        return base

    try:
        sts = base.client("sts", region_name=region_name or "us-east-1")
        params = {
            "RoleArn": role_arn,
            "RoleSessionName": "itam-discovery-session",
        }
        if external_id:
            params["ExternalId"] = external_id
        creds = sts.assume_role(**params)["Credentials"]
        return boto3.Session(
            aws_access_key_id=creds.get("AccessKeyId"),
            aws_secret_access_key=creds.get("SecretAccessKey"),
            aws_session_token=creds.get("SessionToken"),
        )
    except Exception:
        return None


def discover_from_servers_cache(customer_id=None):
    path = _servers_cache_path()
    if not os.path.exists(path):
        return []

    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT instance, location, customer_name, os, cpu, mem, disk,
               download, upload, last_update_ts, status
        FROM servers_cache
        """
    )
    rows = cur.fetchall()
    conn.close()

    customer_map = _customer_name_map()
    out = []

    for row in rows:
        instance = norm_str(row[0])
        if not instance:
            continue

        row_customer = customer_map.get(norm_str(row[2]).lower())
        if customer_id is not None:
            row_customer = customer_id
        if row_customer is None:
            continue

        hostname = norm_hostname(instance)
        primary_ip = maybe_ip_from_text(instance)
        discovered_at = _dt_from_epoch(row[9])

        record = {
            "source_name": "servers_cache",
            "asset_type_hint": "server",
            "asset_name": hostname or instance,
            "hostname": hostname,
            "primary_ip": primary_ip,
            "location": norm_str(row[1]),
            "os_name": norm_str(row[3]),
            "status": norm_str(row[10]).lower() or "active",
            "metadata": {
                "instance": instance,
                "cpu": row[4],
                "mem": row[5],
                "disk": row[6],
                "download": row[7],
                "upload": row[8],
            },
        }
        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"servers_cache:{instance}",
                "confidence": 85,
                "discovered_at": discovered_at,
                "record": record,
            }
        )

    return out


def discover_from_desktop_cache(customer_id=None):
    path = _desktop_cache_path()
    if not os.path.exists(path):
        return []

    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT host, customer_name, os, cpu, mem, disk, download, upload,
               loss, latency, is_up_to_date, pending_updates, last_update_ts, status
        FROM desktops_cache
        """
    )
    rows = cur.fetchall()
    conn.close()

    customer_map = _customer_name_map()
    out = []

    for row in rows:
        host = norm_str(row[0])
        if not host:
            continue

        row_customer = customer_map.get(norm_str(row[1]).lower())
        if customer_id is not None:
            row_customer = customer_id
        if row_customer is None:
            continue

        discovered_at = _dt_from_epoch(row[12])
        record = {
            "source_name": "desktop_cache",
            "asset_type_hint": "workstation",
            "asset_name": host,
            "hostname": norm_hostname(host),
            "primary_ip": maybe_ip_from_text(host),
            "os_name": norm_str(row[2]),
            "status": norm_str(row[13]).lower() or "active",
            "metadata": {
                "cpu": row[3],
                "mem": row[4],
                "disk": row[5],
                "download": row[6],
                "upload": row[7],
                "loss": row[8],
                "latency": row[9],
                "is_up_to_date": bool(row[10]),
                "pending_updates": row[11],
            },
        }
        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"desktop_cache:{host}",
                "confidence": 80,
                "discovered_at": discovered_at,
                "record": record,
            }
        )

    return out


def discover_from_snmp_configs(customer_id=None):
    query = SnmpConfig.query
    if customer_id is not None:
        query = query.filter(SnmpConfig.customer_id == customer_id)

    out = []
    for row in query.all():
        ip = norm_ip(row.device_ip)
        record = {
            "source_name": "snmp",
            "asset_type_hint": "network_device",
            "asset_name": norm_str(row.name) or row.device_ip,
            "hostname": norm_hostname(row.name) or norm_hostname(row.device_ip),
            "primary_ip": ip,
            "template": norm_str(row.template),
            "platform": "network",
            "metadata": {
                "monitoring_server": norm_str(row.monitoring_server),
                "snmp_version": norm_str(row.snmp_version),
                "port": row.port,
                "template": norm_str(row.template),
                "device_ip": norm_str(row.device_ip),
            },
        }
        out.append(
            {
                "customer_id": row.customer_id,
                "source_key": f"snmp:{row.device_ip}",
                "confidence": 90,
                "discovered_at": row.updated_at or row.created_at or datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out


def discover_from_aws(customer_id=None, accounts=None):
    rows = (
        accounts
        if isinstance(accounts, list) and accounts
        else (_aws_accounts_from_integrations(customer_id) or _aws_accounts_from_env(customer_id))
    )
    out = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        row_customer = customer_id if customer_id is not None else _int_or_none(row.get("customer_id"))
        account_id = norm_str(row.get("account_id"))
        role_arn = norm_str(row.get("role_arn"))
        external_id = norm_str(row.get("external_id"))
        profile = norm_str(row.get("profile"))
        regions = row.get("regions") if isinstance(row.get("regions"), list) else _aws_default_regions()
        regions = _unique_items(regions) or _aws_default_regions()

        session = _make_aws_session(
            profile_name=profile,
            role_arn=role_arn,
            external_id=external_id,
            region_name=regions[0],
        )
        if session is None:
            continue

        if not account_id:
            try:
                sts = session.client("sts", region_name=regions[0])
                ident = sts.get_caller_identity() or {}
                account_id = norm_str(ident.get("Account"))
            except Exception:
                account_id = ""

        for region in regions:
            try:
                ec2 = session.client("ec2", region_name=region)
                paginator = ec2.get_paginator("describe_instances")
                page_iter = paginator.paginate(PaginationConfig={"PageSize": 200})
            except Exception:
                continue

            try:
                for page in page_iter:
                    for res in page.get("Reservations", []) or []:
                        for inst in res.get("Instances", []) or []:
                            instance_id = norm_str(inst.get("InstanceId"))
                            if not instance_id:
                                continue

                            tags = {}
                            for tag in inst.get("Tags", []) or []:
                                if not isinstance(tag, dict):
                                    continue
                                k = norm_str(tag.get("Key"))
                                v = norm_str(tag.get("Value"))
                                if k:
                                    tags[k] = v

                            name_tag = tags.get("Name", "")
                            private_dns = norm_str(inst.get("PrivateDnsName"))
                            public_dns = norm_str(inst.get("PublicDnsName"))
                            private_ip = norm_ip(inst.get("PrivateIpAddress"))
                            public_ip = norm_ip(inst.get("PublicIpAddress"))
                            state_name = norm_str((inst.get("State") or {}).get("Name")).lower() or "unknown"

                            net_rows = []
                            for ni in inst.get("NetworkInterfaces", []) or []:
                                if not isinstance(ni, dict):
                                    continue
                                ni_ip = ""
                                private_ip_set = ni.get("PrivateIpAddresses")
                                if isinstance(private_ip_set, list) and private_ip_set:
                                    ni_ip = norm_ip((private_ip_set[0] or {}).get("PrivateIpAddress"))
                                net_rows.append(
                                    {
                                        "name": norm_str(ni.get("Description"))
                                        or norm_str(ni.get("NetworkInterfaceId"))
                                        or "primary",
                                        "mac": norm_str(ni.get("MacAddress")),
                                        "ip": ni_ip,
                                        "subnet_mask": "",
                                        "gateway": "",
                                        "vlan": "",
                                        "is_primary": bool(ni.get("Attachment", {}).get("DeviceIndex", 1) == 0),
                                    }
                                )

                            primary_mac = ""
                            if net_rows:
                                primary_mac = norm_str(net_rows[0].get("mac"))

                            status = "active" if state_name in {"running", "pending"} else "inactive"
                            discovered_at = datetime.now(timezone.utc)
                            aws_tag_rows = [{"key": k, "value": v} for k, v in tags.items()]

                            record = {
                                "source_name": "cloud_aws",
                                "asset_type_hint": "cloud_asset",
                                "asset_name": name_tag or instance_id,
                                "hostname": norm_hostname(private_dns or public_dns or name_tag or instance_id),
                                "primary_ip": public_ip or private_ip,
                                "primary_mac": primary_mac,
                                "serial_number": "",
                                "vendor": "aws",
                                "model": norm_str(inst.get("InstanceType")),
                                "os_name": norm_str(inst.get("PlatformDetails") or inst.get("Platform") or "linux/unix"),
                                "os_version": "",
                                "platform": "cloud",
                                "domain": "",
                                "location": region,
                                "environment": tags.get("Environment", ""),
                                "status": status,
                                "cloud_instance_id": instance_id,
                                "device_uuid": norm_str(inst.get("ImageId")),
                                "tags": aws_tag_rows,
                                "custom_fields": {
                                    "cloud_provider": "aws",
                                    "aws_account_id": account_id,
                                    "aws_region": region,
                                    "aws_vpc_id": norm_str(inst.get("VpcId")),
                                    "aws_subnet_id": norm_str(inst.get("SubnetId")),
                                    "aws_state": state_name,
                                },
                                "hardware": {
                                    "cpu_cores": (inst.get("CpuOptions") or {}).get("CoreCount"),
                                },
                                "network_interfaces": net_rows,
                                "metadata": {
                                    "cloud_provider": "aws",
                                    "integration_name": norm_str(row.get("integration_name")),
                                    "account_id": account_id,
                                    "region": region,
                                    "instance_id": instance_id,
                                    "instance_type": norm_str(inst.get("InstanceType")),
                                    "image_id": norm_str(inst.get("ImageId")),
                                    "availability_zone": norm_str(
                                        (inst.get("Placement") or {}).get("AvailabilityZone")
                                    ),
                                    "vpc_id": norm_str(inst.get("VpcId")),
                                    "subnet_id": norm_str(inst.get("SubnetId")),
                                    "state": state_name,
                                    "launch_time": (
                                        inst.get("LaunchTime").isoformat()
                                        if inst.get("LaunchTime")
                                        else ""
                                    ),
                                    "public_ip": public_ip,
                                    "private_ip": private_ip,
                                },
                            }

                            out.append(
                                {
                                    "customer_id": row_customer,
                                    "source_key": f"cloud_aws:{account_id}:{region}:{instance_id}",
                                    "confidence": 93,
                                    "discovered_at": discovered_at,
                                    "record": record,
                                }
                            )
            except Exception:
                continue

    return out


def discover_from_cloud_payload(customer_id, assets):
    out = []
    for i, item in enumerate(assets or []):
        if not isinstance(item, dict):
            continue
        source_key = (
            norm_str(item.get("source_key"))
            or norm_str(item.get("cloud_instance_id"))
            or norm_str(item.get("asset_id"))
            or norm_str(item.get("hostname"))
            or norm_str(item.get("primary_ip"))
            or f"cloud_item_{i}"
        )
        if not source_key:
            continue

        discovered_at = datetime.now(timezone.utc)
        record = {
            "source_name": "cloud_manual",
            "asset_type_hint": norm_str(item.get("asset_type_hint")) or "cloud_asset",
            "asset_name": norm_str(item.get("asset_name")) or norm_str(item.get("hostname")) or source_key,
            "hostname": norm_hostname(item.get("hostname") or item.get("asset_name")),
            "primary_ip": norm_ip(item.get("primary_ip")),
            "primary_mac": norm_str(item.get("primary_mac")),
            "serial_number": norm_str(item.get("serial_number")),
            "vendor": norm_str(item.get("vendor")),
            "model": norm_str(item.get("model")),
            "os_name": norm_str(item.get("os_name")),
            "os_version": norm_str(item.get("os_version")),
            "platform": norm_str(item.get("platform")) or "cloud",
            "domain": norm_str(item.get("domain")),
            "location": norm_str(item.get("location")),
            "environment": norm_str(item.get("environment")),
            "status": norm_str(item.get("status")) or "active",
            "cloud_instance_id": norm_str(item.get("cloud_instance_id")),
            "device_uuid": norm_str(item.get("device_uuid")),
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
            "custom_fields": (
                item.get("custom_fields") if isinstance(item.get("custom_fields"), dict) else {}
            ),
            "software": item.get("software") if isinstance(item.get("software"), list) else [],
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        }
        out.append(
            {
                "customer_id": customer_id,
                "source_key": f"cloud_manual:{source_key}",
                "confidence": int(item.get("confidence") or 70),
                "discovered_at": discovered_at,
                "record": record,
            }
        )
    return out


def _azure_subscriptions_from_integrations(customer_id=None):
    rows = _cloud_integrations_from_db("azure", customer_id=customer_id)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "customer_id": customer_id
                if customer_id is not None
                else _int_or_none(row.get("customer_id")),
                "subscription_id": norm_str(row.get("subscription_id")),
                "tenant_id": norm_str(row.get("tenant_id")),
                "client_id": norm_str(row.get("client_id")),
                "client_secret": norm_str(row.get("client_secret")),
                "integration_name": norm_str(row.get("integration_name")),
            }
        )
    return out


def _azure_subscriptions_from_env(customer_id=None):
    raw_json = norm_str(os.environ.get("ITAM_AZURE_SUBSCRIPTIONS_JSON"))
    out = []
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            rows = parsed if isinstance(parsed, list) else [parsed]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                out.append(
                    {
                        "customer_id": customer_id
                        if customer_id is not None
                        else _int_or_none(row.get("customer_id")),
                        "subscription_id": norm_str(row.get("subscription_id")),
                        "tenant_id": norm_str(row.get("tenant_id")),
                        "client_id": norm_str(row.get("client_id")),
                        "client_secret": norm_str(row.get("client_secret")),
                        "integration_name": norm_str(row.get("integration_name")),
                    }
                )
        except Exception:
            out = []
    if out:
        return out

    out.append(
        {
            "customer_id": customer_id
            if customer_id is not None
            else _int_or_none(os.environ.get("ITAM_AZURE_CUSTOMER_ID")),
            "subscription_id": norm_str(os.environ.get("ITAM_AZURE_SUBSCRIPTION_ID")),
            "tenant_id": norm_str(os.environ.get("ITAM_AZURE_TENANT_ID")),
            "client_id": norm_str(os.environ.get("ITAM_AZURE_CLIENT_ID")),
            "client_secret": norm_str(os.environ.get("ITAM_AZURE_CLIENT_SECRET")),
            "integration_name": "",
        }
    )
    return out


def _azure_resource_group_from_id(resource_id):
    rid = norm_str(resource_id).strip("/")
    if not rid:
        return ""
    parts = rid.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "resourcegroups" and i + 1 < len(parts):
            return norm_str(parts[i + 1])
    return ""


def _azure_name_from_id(resource_id):
    rid = norm_str(resource_id).strip("/")
    if not rid:
        return ""
    return norm_str(rid.split("/")[-1])


def _make_azure_credential(tenant_id="", client_id="", client_secret=""):
    try:
        from azure.identity import ClientSecretCredential, DefaultAzureCredential  # type: ignore
    except Exception:
        return None

    if tenant_id and client_id and client_secret:
        try:
            return ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        except Exception:
            return None
    try:
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)
    except Exception:
        return None


def _azure_power_state(compute_client, resource_group, vm_name):
    if not resource_group or not vm_name:
        return ""
    try:
        view = compute_client.virtual_machines.instance_view(resource_group, vm_name)
        for status in getattr(view, "statuses", []) or []:
            code = norm_str(getattr(status, "code", ""))
            if code.lower().startswith("powerstate/"):
                return code.split("/", 1)[1].lower()
    except Exception:
        return ""
    return ""


def discover_from_azure(customer_id=None, subscriptions=None):
    rows = (
        subscriptions
        if isinstance(subscriptions, list) and subscriptions
        else (_azure_subscriptions_from_integrations(customer_id) or _azure_subscriptions_from_env(customer_id))
    )
    out = []

    try:
        from azure.mgmt.compute import ComputeManagementClient  # type: ignore
        from azure.mgmt.network import NetworkManagementClient  # type: ignore
    except Exception:
        return []

    for row in rows:
        if not isinstance(row, dict):
            continue

        row_customer = customer_id if customer_id is not None else _int_or_none(row.get("customer_id"))
        subscription_id = norm_str(row.get("subscription_id"))
        if not subscription_id:
            continue

        credential = _make_azure_credential(
            tenant_id=norm_str(row.get("tenant_id")),
            client_id=norm_str(row.get("client_id")),
            client_secret=norm_str(row.get("client_secret")),
        )
        if credential is None:
            continue

        try:
            compute_client = ComputeManagementClient(credential, subscription_id)
            network_client = NetworkManagementClient(credential, subscription_id)
            vm_rows = compute_client.virtual_machines.list_all()
        except Exception:
            continue

        for vm in vm_rows or []:
            try:
                vm_name = norm_str(getattr(vm, "name", ""))
                vm_res_id = norm_str(getattr(vm, "id", ""))
                vm_id = norm_str(getattr(vm, "vm_id", "")) or vm_name
                if not vm_id:
                    continue

                resource_group = _azure_resource_group_from_id(vm_res_id)
                location = norm_str(getattr(vm, "location", ""))
                tags = getattr(vm, "tags", None) if isinstance(getattr(vm, "tags", None), dict) else {}
                hw = getattr(vm, "hardware_profile", None)
                vm_size = norm_str(getattr(hw, "vm_size", ""))
                os_disk = getattr(getattr(vm, "storage_profile", None), "os_disk", None)
                os_type = norm_str(getattr(os_disk, "os_type", ""))
                power_state = _azure_power_state(compute_client, resource_group, vm_name)

                net_rows = []
                public_ip = ""
                private_ip = ""
                nic_refs = getattr(getattr(vm, "network_profile", None), "network_interfaces", None) or []
                for idx, nic_ref in enumerate(nic_refs):
                    nic_id = norm_str(getattr(nic_ref, "id", ""))
                    nic_rg = _azure_resource_group_from_id(nic_id)
                    nic_name = _azure_name_from_id(nic_id)
                    nic_mac = ""
                    nic_ip = ""
                    nic_pub_ip = ""
                    if nic_rg and nic_name:
                        try:
                            nic_obj = network_client.network_interfaces.get(nic_rg, nic_name)
                            nic_mac = norm_str(getattr(nic_obj, "mac_address", ""))
                            ip_cfgs = getattr(nic_obj, "ip_configurations", None) or []
                            if ip_cfgs:
                                cfg = ip_cfgs[0]
                                nic_ip = norm_ip(getattr(cfg, "private_ip_address", ""))
                                pub_ref = getattr(cfg, "public_ip_address", None)
                                pub_id = norm_str(getattr(pub_ref, "id", ""))
                                if pub_id:
                                    pub_rg = _azure_resource_group_from_id(pub_id)
                                    pub_name = _azure_name_from_id(pub_id)
                                    if pub_rg and pub_name:
                                        try:
                                            pub_obj = network_client.public_ip_addresses.get(pub_rg, pub_name)
                                            nic_pub_ip = norm_ip(getattr(pub_obj, "ip_address", ""))
                                        except Exception:
                                            nic_pub_ip = ""
                        except Exception:
                            pass

                    if not private_ip and nic_ip:
                        private_ip = nic_ip
                    if not public_ip and nic_pub_ip:
                        public_ip = nic_pub_ip

                    net_rows.append(
                        {
                            "name": nic_name or f"nic-{idx}",
                            "mac": nic_mac,
                            "ip": nic_ip,
                            "subnet_mask": "",
                            "gateway": "",
                            "vlan": "",
                            "is_primary": bool(idx == 0),
                        }
                    )

                primary_mac = norm_str(net_rows[0].get("mac")) if net_rows else ""
                normalized_power = power_state or norm_str(getattr(vm, "provisioning_state", "")).lower()
                status = "active" if normalized_power in {"running", "starting"} else "inactive"
                discovered_at = datetime.now(timezone.utc)
                tag_rows = [{"key": k, "value": v} for k, v in (tags or {}).items()]

                record = {
                    "source_name": "cloud_azure",
                    "asset_type_hint": "cloud_asset",
                    "asset_name": vm_name or vm_id,
                    "hostname": norm_hostname(vm_name or vm_id),
                    "primary_ip": public_ip or private_ip,
                    "primary_mac": primary_mac,
                    "serial_number": "",
                    "vendor": "azure",
                    "model": vm_size,
                    "os_name": os_type or "unknown",
                    "os_version": "",
                    "platform": "cloud",
                    "domain": "",
                    "location": location,
                    "environment": tags.get("Environment", "") if isinstance(tags, dict) else "",
                    "status": status,
                    "cloud_instance_id": vm_id,
                    "device_uuid": vm_res_id,
                    "tags": tag_rows,
                    "custom_fields": {
                        "cloud_provider": "azure",
                        "azure_subscription_id": subscription_id,
                        "azure_resource_group": resource_group,
                        "azure_power_state": normalized_power,
                        "integration_name": norm_str(row.get("integration_name")),
                    },
                    "network_interfaces": net_rows,
                    "metadata": {
                        "cloud_provider": "azure",
                        "integration_name": norm_str(row.get("integration_name")),
                        "subscription_id": subscription_id,
                        "resource_group": resource_group,
                        "location": location,
                        "vm_id": vm_id,
                        "vm_name": vm_name,
                        "vm_size": vm_size,
                        "power_state": normalized_power,
                        "private_ip": private_ip,
                        "public_ip": public_ip,
                    },
                }

                out.append(
                    {
                        "customer_id": row_customer,
                        "source_key": f"cloud_azure:{subscription_id}:{resource_group}:{vm_id}",
                        "confidence": 92,
                        "discovered_at": discovered_at,
                        "record": record,
                    }
                )
            except Exception:
                continue
    return out


def _gcp_projects_from_integrations(customer_id=None):
    rows = _cloud_integrations_from_db("gcp", customer_id=customer_id)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "customer_id": customer_id
                if customer_id is not None
                else _int_or_none(row.get("customer_id")),
                "project_id": norm_str(row.get("project_id")),
                "credentials_json": row.get("credentials_json"),
                "credentials_file": norm_str(row.get("credentials_file")),
                "integration_name": norm_str(row.get("integration_name")),
            }
        )
    return out


def _gcp_projects_from_env(customer_id=None):
    raw_json = norm_str(os.environ.get("ITAM_GCP_PROJECTS_JSON"))
    out = []
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            rows = parsed if isinstance(parsed, list) else [parsed]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                out.append(
                    {
                        "customer_id": customer_id
                        if customer_id is not None
                        else _int_or_none(row.get("customer_id")),
                        "project_id": norm_str(row.get("project_id")),
                        "credentials_json": row.get("credentials_json"),
                        "credentials_file": norm_str(row.get("credentials_file")),
                        "integration_name": norm_str(row.get("integration_name")),
                    }
                )
        except Exception:
            out = []
    if out:
        return out

    out.append(
        {
            "customer_id": customer_id
            if customer_id is not None
            else _int_or_none(os.environ.get("ITAM_GCP_CUSTOMER_ID")),
            "project_id": norm_str(os.environ.get("ITAM_GCP_PROJECT_ID")),
            "credentials_json": norm_str(os.environ.get("ITAM_GCP_CREDENTIALS_JSON")),
            "credentials_file": norm_str(os.environ.get("ITAM_GCP_CREDENTIALS_FILE")),
            "integration_name": "",
        }
    )
    return out


def _gcp_credential_from_config(credentials_json=None, credentials_file=""):
    try:
        import google.auth  # type: ignore
        from google.oauth2 import service_account  # type: ignore
    except Exception:
        return None

    if isinstance(credentials_json, dict):
        try:
            return service_account.Credentials.from_service_account_info(credentials_json)
        except Exception:
            return None

    if isinstance(credentials_json, str) and credentials_json.strip():
        try:
            payload = json.loads(credentials_json)
            if isinstance(payload, dict):
                return service_account.Credentials.from_service_account_info(payload)
        except Exception:
            return None

    if credentials_file:
        try:
            return service_account.Credentials.from_service_account_file(credentials_file)
        except Exception:
            return None

    try:
        creds, _project_id = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"]
        )
        return creds
    except Exception:
        return None


def _gcp_attr(obj, *names):
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value not in (None, ""):
            return value
    return None


def discover_from_gcp(customer_id=None, projects=None):
    rows = (
        projects
        if isinstance(projects, list) and projects
        else (_gcp_projects_from_integrations(customer_id) or _gcp_projects_from_env(customer_id))
    )
    out = []

    try:
        from google.cloud import compute_v1  # type: ignore
    except Exception:
        return []

    for row in rows:
        if not isinstance(row, dict):
            continue

        row_customer = customer_id if customer_id is not None else _int_or_none(row.get("customer_id"))
        project_id = norm_str(row.get("project_id"))
        if not project_id:
            continue

        credentials = _gcp_credential_from_config(
            credentials_json=row.get("credentials_json"),
            credentials_file=norm_str(row.get("credentials_file")),
        )
        if credentials is None:
            continue

        try:
            client = compute_v1.InstancesClient(credentials=credentials)
            agg_iter = client.aggregated_list(project=project_id)
        except Exception:
            continue

        for zone_name, scoped_list in agg_iter:
            instances = getattr(scoped_list, "instances", None) or []
            for inst in instances:
                try:
                    instance_id = norm_str(_gcp_attr(inst, "id"))
                    name = norm_str(_gcp_attr(inst, "name"))
                    if not instance_id and not name:
                        continue
                    if not instance_id:
                        instance_id = name

                    labels = _gcp_attr(inst, "labels")
                    if not isinstance(labels, dict):
                        labels = {}

                    raw_zone = norm_str(_gcp_attr(inst, "zone"))
                    zone = raw_zone.split("/")[-1] if raw_zone else zone_name.split("/")[-1]
                    machine_type = norm_str(_gcp_attr(inst, "machine_type")).split("/")[-1]
                    status_raw = norm_str(_gcp_attr(inst, "status")).lower() or "unknown"
                    net_rows = []
                    private_ip = ""
                    public_ip = ""
                    primary_mac = ""

                    interfaces = _gcp_attr(inst, "network_interfaces") or []
                    for idx, nic in enumerate(interfaces):
                        nic_name = norm_str(_gcp_attr(nic, "name")) or f"nic-{idx}"
                        nic_mac = norm_str(_gcp_attr(nic, "mac_address", "macAddress"))
                        nic_ip = norm_ip(_gcp_attr(nic, "network_i_p", "networkIP"))

                        if not private_ip and nic_ip:
                            private_ip = nic_ip
                        if not primary_mac and nic_mac:
                            primary_mac = nic_mac

                        nic_public = ""
                        access_configs = _gcp_attr(nic, "access_configs", "accessConfigs") or []
                        for ac in access_configs:
                            nat_ip = norm_ip(_gcp_attr(ac, "nat_i_p", "natIP"))
                            if nat_ip:
                                nic_public = nat_ip
                                break
                        if not public_ip and nic_public:
                            public_ip = nic_public

                        net_rows.append(
                            {
                                "name": nic_name,
                                "mac": nic_mac,
                                "ip": nic_ip,
                                "subnet_mask": "",
                                "gateway": "",
                                "vlan": "",
                                "is_primary": bool(idx == 0),
                            }
                        )

                    status = "active" if status_raw in {"running", "staging", "provisioning"} else "inactive"
                    discovered_at = datetime.now(timezone.utc)
                    tag_rows = [{"key": k, "value": v} for k, v in labels.items()]

                    record = {
                        "source_name": "cloud_gcp",
                        "asset_type_hint": "cloud_asset",
                        "asset_name": name or instance_id,
                        "hostname": norm_hostname(name or instance_id),
                        "primary_ip": public_ip or private_ip,
                        "primary_mac": primary_mac,
                        "serial_number": "",
                        "vendor": "gcp",
                        "model": machine_type,
                        "os_name": "unknown",
                        "os_version": "",
                        "platform": "cloud",
                        "domain": "",
                        "location": zone,
                        "environment": labels.get("environment", ""),
                        "status": status,
                        "cloud_instance_id": instance_id,
                        "device_uuid": norm_str(_gcp_attr(inst, "self_link", "selfLink")),
                        "tags": tag_rows,
                        "custom_fields": {
                            "cloud_provider": "gcp",
                            "gcp_project_id": project_id,
                            "gcp_zone": zone,
                            "gcp_status": status_raw,
                            "integration_name": norm_str(row.get("integration_name")),
                        },
                        "network_interfaces": net_rows,
                        "metadata": {
                            "cloud_provider": "gcp",
                            "integration_name": norm_str(row.get("integration_name")),
                            "project_id": project_id,
                            "zone": zone,
                            "instance_id": instance_id,
                            "instance_name": name,
                            "machine_type": machine_type,
                            "status": status_raw,
                            "private_ip": private_ip,
                            "public_ip": public_ip,
                        },
                    }

                    out.append(
                        {
                            "customer_id": row_customer,
                            "source_key": f"cloud_gcp:{project_id}:{zone}:{instance_id}",
                            "confidence": 92,
                            "discovered_at": discovered_at,
                            "record": record,
                        }
                    )
                except Exception:
                    continue

    return out


def _ot_endpoints_from_env(var_name, customer_id=None):
    raw = norm_str(os.environ.get(var_name))
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []

    rows = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = customer_id if customer_id is not None else _int_or_none(row.get("customer_id"))
        if cid is None:
            continue
        item = dict(row)
        item["customer_id"] = cid
        out.append(item)
    return out


def _tcp_alive(host, port, timeout=2.0):
    host = norm_str(host)
    if not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _bacnet_probe(host, port=47808, timeout=1.5):
    host = norm_str(host)
    if not host:
        return False
    # Minimal BACnet/IP BVLC+NPDU Who-Is packet.
    who_is = bytes([0x81, 0x0B, 0x00, 0x0C, 0x01, 0x20, 0xFF, 0xFF, 0x00, 0x08, 0x01, 0x00])
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(who_is, (host, int(port)))
        _data, _addr = sock.recvfrom(512)
        sock.close()
        return True
    except Exception:
        return False


def _ot_endpoint_record(item, protocol, source_name, fallback_name):
    host = norm_str(item.get("host") or item.get("ip") or item.get("address"))
    asset_name = norm_str(item.get("asset_name") or item.get("name")) or f"{fallback_name}-{host or 'device'}"
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "asset_name": asset_name,
        "hostname": norm_str(item.get("hostname")) or host or asset_name,
        "primary_ip": norm_ip(item.get("primary_ip") or host),
        "primary_mac": norm_str(item.get("primary_mac")),
        "serial_number": norm_str(item.get("serial_number")),
        "vendor": norm_str(item.get("vendor")),
        "model": norm_str(item.get("model")),
        "os_name": norm_str(item.get("os_name")),
        "os_version": norm_str(item.get("os_version")),
        "location": norm_str(item.get("location")),
        "environment": norm_str(item.get("environment")),
        "status": norm_str(item.get("status")) or "inactive",
        "protocol": protocol,
        "platform": norm_str(item.get("platform")) or "ot",
        "custom_fields": item.get("custom_fields") if isinstance(item.get("custom_fields"), dict) else {},
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "metadata": {
            **metadata,
            "ot_protocol": protocol,
            "ot_endpoint_host": host,
            "ot_source": source_name,
        },
    }


def discover_from_ot_modbus(customer_id=None, endpoints=None):
    rows = endpoints if isinstance(endpoints, list) and endpoints else _ot_endpoints_from_env(
        "ITAM_OT_MODBUS_ENDPOINTS_JSON",
        customer_id=customer_id,
    )
    out = []

    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        row_customer = customer_id if customer_id is not None else _int_or_none(item.get("customer_id"))
        if row_customer is None:
            continue

        host = norm_str(item.get("host") or item.get("ip") or item.get("address"))
        port = _int_or_none(item.get("port")) or 502
        unit_id = _int_or_none(item.get("unit_id") or item.get("slave_id")) or 1
        register = _int_or_none(item.get("register")) or 0
        status = "inactive"
        probe_value = None

        try:
            from pymodbus.client import ModbusTcpClient  # type: ignore
        except Exception:
            ModbusTcpClient = None

        alive = False
        if ModbusTcpClient:
            try:
                client = ModbusTcpClient(host=host, port=port, timeout=2)
                alive = bool(client.connect())
                if alive:
                    status = "active"
                    try:
                        rr = client.read_holding_registers(address=register, count=1, slave=unit_id)
                        if rr and hasattr(rr, "registers") and rr.registers:
                            probe_value = rr.registers[0]
                    except Exception:
                        probe_value = None
                client.close()
            except Exception:
                alive = False
        else:
            alive = _tcp_alive(host, port, timeout=2)
            if alive:
                status = "active"

        base = _ot_endpoint_record(item, protocol="modbus", source_name="ot_modbus", fallback_name="modbus")
        base["status"] = status
        base_meta = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
        base_meta.update(
            {
                "ot_modbus_port": port,
                "ot_modbus_unit_id": unit_id,
                "ot_modbus_probe_register": register,
                "ot_modbus_probe_value": probe_value,
                "ot_modbus_alive": bool(alive),
            }
        )
        base["metadata"] = base_meta

        record, source_key = _normalize_ot_record(
            item=base,
            source_name="ot_modbus",
            fallback_key=f"ot_modbus_{i}",
        )
        if not record:
            continue

        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"ot_modbus:{source_key}:{host}:{port}:{unit_id}",
                "confidence": int(item.get("confidence") or (82 if alive else 70)),
                "discovered_at": datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out


def discover_from_ot_bacnet(customer_id=None, endpoints=None):
    rows = endpoints if isinstance(endpoints, list) and endpoints else _ot_endpoints_from_env(
        "ITAM_OT_BACNET_ENDPOINTS_JSON",
        customer_id=customer_id,
    )
    out = []

    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        row_customer = customer_id if customer_id is not None else _int_or_none(item.get("customer_id"))
        if row_customer is None:
            continue

        host = norm_str(item.get("host") or item.get("ip") or item.get("address"))
        port = _int_or_none(item.get("port")) or 47808
        device_id = _int_or_none(item.get("device_id"))
        alive = _bacnet_probe(host, port=port, timeout=1.5)

        base = _ot_endpoint_record(item, protocol="bacnet", source_name="ot_bacnet", fallback_name="bacnet")
        base["status"] = "active" if alive else (norm_str(item.get("status")) or "inactive")
        base_meta = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
        base_meta.update(
            {
                "ot_bacnet_port": port,
                "ot_bacnet_device_id": device_id,
                "ot_bacnet_alive": bool(alive),
            }
        )
        base["metadata"] = base_meta

        record, source_key = _normalize_ot_record(
            item=base,
            source_name="ot_bacnet",
            fallback_key=f"ot_bacnet_{i}",
        )
        if not record:
            continue

        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"ot_bacnet:{source_key}:{host}:{port}:{device_id or ''}",
                "confidence": int(item.get("confidence") or (78 if alive else 68)),
                "discovered_at": datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out


def discover_from_ot_opcua(customer_id=None, endpoints=None):
    rows = endpoints if isinstance(endpoints, list) and endpoints else _ot_endpoints_from_env(
        "ITAM_OT_OPCUA_ENDPOINTS_JSON",
        customer_id=customer_id,
    )
    out = []

    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        row_customer = customer_id if customer_id is not None else _int_or_none(item.get("customer_id"))
        if row_customer is None:
            continue

        endpoint = norm_str(item.get("endpoint"))
        host = norm_str(item.get("host") or item.get("ip"))
        port = _int_or_none(item.get("port")) or 4840

        if endpoint:
            try:
                parsed = urlparse(endpoint)
                if parsed.hostname:
                    host = parsed.hostname
                if parsed.port:
                    port = int(parsed.port)
            except Exception:
                pass
        if not endpoint and host:
            endpoint = f"opc.tcp://{host}:{port}"

        server_time = ""
        alive = False
        try:
            from opcua import Client as OpcUaClient  # type: ignore
        except Exception:
            OpcUaClient = None

        if OpcUaClient and endpoint:
            try:
                client = OpcUaClient(endpoint, timeout=2)
                client.connect()
                alive = True
                try:
                    server_time = str(client.get_node("i=2258").get_value())
                except Exception:
                    server_time = ""
                client.disconnect()
            except Exception:
                alive = False
        elif host:
            alive = _tcp_alive(host, port, timeout=2)

        base = _ot_endpoint_record(item, protocol="opcua", source_name="ot_opcua", fallback_name="opcua")
        base["status"] = "active" if alive else (norm_str(item.get("status")) or "inactive")
        base_meta = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
        base_meta.update(
            {
                "ot_opcua_endpoint": endpoint,
                "ot_opcua_server_time": server_time,
                "ot_opcua_alive": bool(alive),
            }
        )
        base["metadata"] = base_meta

        record, source_key = _normalize_ot_record(
            item=base,
            source_name="ot_opcua",
            fallback_key=f"ot_opcua_{i}",
        )
        if not record:
            continue

        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"ot_opcua:{source_key}:{endpoint or host}",
                "confidence": int(item.get("confidence") or (83 if alive else 70)),
                "discovered_at": datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out


def _normalize_ot_status(value):
    s = norm_str(value).strip().lower()
    if not s:
        return "active"
    if s in {"up", "running", "online", "healthy", "active"}:
        return "active"
    if s in {"down", "offline", "inactive", "fault"}:
        return "inactive"
    return s


def _normalize_ot_record(item, source_name, fallback_key):
    if not isinstance(item, dict):
        return None, ""

    source_key = (
        norm_str(item.get("source_key"))
        or norm_str(item.get("asset_id"))
        or norm_str(item.get("device_id"))
        or norm_str(item.get("hostname"))
        or norm_str(item.get("primary_ip"))
        or fallback_key
    )
    if not source_key:
        return None, ""

    tags = item.get("tags")
    if isinstance(tags, dict):
        tags = [{"key": k, "value": v} for k, v in tags.items()]
    if not isinstance(tags, list):
        tags = []

    protocol = norm_str(item.get("protocol") or item.get("ot_protocol")).lower() or "unknown"
    platform = norm_str(item.get("platform")) or "ot"
    network_rows = item.get("network_interfaces") if isinstance(item.get("network_interfaces"), list) else []

    record = {
        "source_name": source_name,
        "asset_type_hint": "ot_device",
        "asset_name": norm_str(item.get("asset_name")) or norm_str(item.get("hostname")) or source_key,
        "hostname": norm_hostname(item.get("hostname") or item.get("asset_name") or source_key),
        "primary_ip": norm_ip(item.get("primary_ip")),
        "primary_mac": norm_str(item.get("primary_mac")),
        "serial_number": norm_str(item.get("serial_number")),
        "vendor": norm_str(item.get("vendor")),
        "model": norm_str(item.get("model")),
        "os_name": norm_str(item.get("os_name")),
        "os_version": norm_str(item.get("os_version")),
        "platform": platform,
        "domain": norm_str(item.get("domain")),
        "location": norm_str(item.get("location")),
        "environment": norm_str(item.get("environment")),
        "status": _normalize_ot_status(item.get("status")),
        "device_uuid": norm_str(item.get("device_uuid")),
        "tags": tags,
        "custom_fields": (
            item.get("custom_fields") if isinstance(item.get("custom_fields"), dict) else {}
        ),
        "software": item.get("software") if isinstance(item.get("software"), list) else [],
        "network_interfaces": network_rows,
        "metadata": {
            **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
            "ot_protocol": protocol,
            "ot_source": source_name,
        },
    }
    return record, source_key


def discover_from_ot_payload(customer_id, assets):
    out = []
    for i, item in enumerate(assets or []):
        record, source_key = _normalize_ot_record(
            item=item,
            source_name="ot_manual",
            fallback_key=f"ot_item_{i}",
        )
        if not record:
            continue
        out.append(
            {
                "customer_id": customer_id,
                "source_key": f"ot_manual:{source_key}",
                "confidence": int(item.get("confidence") or 72) if isinstance(item, dict) else 72,
                "discovered_at": datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out


def discover_from_ot_seed(customer_id=None):
    raw = norm_str(os.environ.get("ITAM_OT_ASSETS_JSON"))
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []

    rows = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        row_customer = customer_id if customer_id is not None else _int_or_none(item.get("customer_id"))
        if row_customer is None:
            continue

        record, source_key = _normalize_ot_record(
            item=item,
            source_name="ot_seed",
            fallback_key=f"ot_seed_{i}",
        )
        if not record:
            continue

        out.append(
            {
                "customer_id": row_customer,
                "source_key": f"ot_seed:{source_key}",
                "confidence": int(item.get("confidence") or 75),
                "discovered_at": datetime.now(timezone.utc),
                "record": record,
            }
        )
    return out
