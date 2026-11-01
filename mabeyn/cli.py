import argparse
import asyncio
import getpass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from mabeyn.core.config import get_settings
from mabeyn.core.tracking import export_tracking_env
from mabeyn.db.provisioning import create_tenant
from mabeyn.llm.client import get_llm
from mabeyn.seed import seed_kargoshop


async def _create_tenant(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Admin password: ")
    engine = create_async_engine(get_settings().admin_database_url)
    try:
        tenant_id = await create_tenant(engine, args.name, args.slug, args.admin_email, password)
    except (ValueError, IntegrityError) as exc:
        print(f"error: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        await engine.dispose()
    print(f"created tenant {args.slug} ({tenant_id})")
    return 0


async def _seed_kargoshop(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Demo admin password: ")
    engine = create_async_engine(get_settings().admin_database_url)
    try:
        tenant_id = await seed_kargoshop(engine, get_llm(), password)
    except (ValueError, IntegrityError) as exc:
        print(f"error: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        await engine.dispose()
    print(f"kargoshop is ready ({tenant_id})")
    return 0


def main() -> int:
    export_tracking_env()
    parser = argparse.ArgumentParser(prog="mabeyn")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-tenant", help="create a tenant and its first admin")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--admin-email", required=True)
    create.add_argument("--password", help="prompted when omitted")
    create.set_defaults(handler=_create_tenant)

    seed = commands.add_parser(
        "seed-kargoshop", help="create the demo tenant and ingest its documents"
    )
    seed.add_argument("--password", help="prompted when omitted")
    seed.set_defaults(handler=_seed_kargoshop)

    args = parser.parse_args()
    exit_code: int = asyncio.run(args.handler(args))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
