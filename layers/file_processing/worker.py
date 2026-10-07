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
    from layers.file_processing.runner import task_scope, emit_progress
    def progress(event):
        with (Path(sys.argv[1]).parent / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
    scope_manager = task_scope(progress=progress)
    scope_manager.__enter__()
    emit_progress("recognizing")
    if envelope["kind"] == "pdf":
        from layers.file_processing.pdf import pdf_processor
        from layers.file_processing.models import FileProcessingRequest
        request = FileProcessingRequest.model_validate(payload)
        result = pdf_processor._execute_inline(request)
        if result.success:
            emit_progress("validating")
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
    elif envelope["kind"] == "sections":
        from layers.document_sections import chunk_section_paths
        from layers.document_loader import _DocxChunk
        data = chunk_section_paths(payload["text"],
            [_DocxChunk(item["text"], item["source_text"]) for item in payload["chunks"]],
            source_path=payload["source_path"], source_name=payload["source_name"])
    else:
        raise ValueError("unknown_worker_kind")
    Path(sys.argv[2]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    scope_manager.__exit__(None, None, None)


if __name__ == "__main__":
    main()
