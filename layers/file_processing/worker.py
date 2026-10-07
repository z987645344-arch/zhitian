"""文件工作进程的JSON协议入口；不导入API、向量模型或业务数据库。"""

import json
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils import logger
    logger._configured = True  # 工作进程不创建data/logs；父进程负责结构化结果日志。
    import config
    config.FILE_PROCESSING_WORKER = True
    envelope = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for name, value in envelope["limits"].items():
        setattr(config, name, value)
    payload = envelope["payload"]
    if envelope["kind"] == "pdf":
        from layers.file_processing.pdf import pdf_processor
        from layers.file_processing.models import FileProcessingRequest
        request = FileProcessingRequest.model_validate(payload)
        result = pdf_processor._execute_inline(request)
        if result.success:
            quality = pdf_processor.validate_output(request, result)
            if not quality.passed:
                pdf_processor.cleanup(request, result)
                result = pdf_processor._failed(quality.issues[0].code if quality.issues else "quality_check_failed",
                                               "处理产物质量检查未通过")
            else:
                result.quality_checked = True
        data = result.model_dump(mode="json")
        data["quality_checked"] = result.quality_checked
        data["artifacts"] = [dict(item.model_dump(mode="json"), output_path=item.output_path)
                             for item in result.artifacts]
    elif envelope["kind"] == "document":
        from layers.document_loader import _read_docx
        text = _read_docx(payload["path"])
        data = dict(text=str(text), tables=text.tables)
    elif envelope["kind"] == "quality":
        from layers.file_processing.models import FileArtifact, QualityProfile
        from layers.file_processing.quality import FileQualityChecker
        artifact = FileArtifact.model_validate(payload["artifact"])
        quality = FileQualityChecker().validate(artifact, QualityProfile(payload["profile"]),
                                               **payload.get("limits", {}))
        data = quality.model_dump(mode="json")
        if quality.artifact:
            data["artifact"]["output_path"] = quality.artifact.output_path
    else:
        raise ValueError("unknown_worker_kind")
    Path(sys.argv[2]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
