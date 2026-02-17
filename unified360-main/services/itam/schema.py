from sqlalchemy import inspect

from extensions import db
from models.itam import (
    ItamCloudIntegration,
    ItamAssetHardware,
    ItamAssetLifecycle,
    ItamAssetNetworkInterface,
    ItamAssetTag,
    ItamComplianceFinding,
    ItamCompliancePolicy,
    ItamComplianceRun,
)


_SCHEMA_READY = False


def ensure_phase2_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return []

    engine = db.engine
    inspector = inspect(engine)

    models_by_table = {
        ItamCloudIntegration.__tablename__: ItamCloudIntegration,
        ItamAssetHardware.__tablename__: ItamAssetHardware,
        ItamAssetNetworkInterface.__tablename__: ItamAssetNetworkInterface,
        ItamAssetTag.__tablename__: ItamAssetTag,
        ItamAssetLifecycle.__tablename__: ItamAssetLifecycle,
        ItamCompliancePolicy.__tablename__: ItamCompliancePolicy,
        ItamComplianceRun.__tablename__: ItamComplianceRun,
        ItamComplianceFinding.__tablename__: ItamComplianceFinding,
    }

    missing = []
    for table_name, model_cls in models_by_table.items():
        if inspector.has_table(table_name):
            continue
        model_cls.__table__.create(bind=engine, checkfirst=True)
        missing.append(table_name)

    _SCHEMA_READY = True
    return missing
