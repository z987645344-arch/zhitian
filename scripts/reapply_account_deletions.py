"""恢复后的停机注销核对；独立进程避免污染正在运行的Python数据路径。"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--confirm-service-stopped", action="store_true")
    args = parser.parse_args()
    from scripts import backup_data
    backup_data._require_service_stopped(args.confirm_service_stopped)
    target = args.data_dir.resolve()
    import config
    config.BASE_DIR = str(target.parent)
    config._ACCOUNT_MAINTENANCE_DATA_DIR = str(target)
    config.HISTORY_DB_PATH = str(target / "history.db")
    config.VECTORDB_PATH = str(target / "vectordb")
    from layers import auth, memory, embedding, account_lifecycle
    auth.USERS_DB_PATH = str(target / "users.db")
    # 仅删除现有向量，不做编码或推理；get_collection/delete不需要嵌入模型。
    embedding.get_embedding_function = lambda: None
    try:
        return 0 if account_lifecycle.reconcile_deleted_accounts() else 1
    finally:
        memory.close_resources()


if __name__ == "__main__":
    raise SystemExit(main())
