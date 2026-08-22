# -*- coding: utf-8 -*-
"""文件处理器层间传递的结构化模型。"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FileTaskType(str, Enum):
    WRITE_TEXT = "write_text"
    CONVERT = "convert"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_TABLES = "extract_tables"
    RENDER_PAGES = "render_pages"
    MERGE = "merge"
    SPLIT = "split"


class FileProcessingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class QualityProfile(str, Enum):
    TEXT = "text"
    PDF = "pdf"
    PNG = "png"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"


class FileOwnershipContext(BaseModel):
    """产物持久化所需归属信息；处理器只接收，不自行推断。"""

    owner_user_id: str
    session_id: Optional[str] = None
    organization_id: Optional[int] = None
    source_task_id: Optional[str] = None


class FileProcessingRequest(BaseModel):
    """统一执行请求；输出位置必须由上层提供。"""

    task_type: FileTaskType
    source_paths: List[str] = Field(default_factory=list)
    source_format: str = ""
    target_format: str = ""
    output_path: Optional[str] = None
    output_dir: Optional[str] = None
    content: Optional[str] = None
    max_input_size_bytes: int = 0
    max_output_size_bytes: int = 0
    max_pages: int = 0
    ownership: Optional[FileOwnershipContext] = None

    @field_validator("source_format", "target_format", mode="before")
    @classmethod
    def normalize_format(cls, value: object) -> str:
        return str(value or "").strip().lower().lstrip(".")

    @model_validator(mode="after")
    def validate_destination(self) -> "FileProcessingRequest":
        if self.task_type in {FileTaskType.WRITE_TEXT, FileTaskType.MERGE}:
            if not self.output_path:
                raise ValueError("output_path_required")
        if self.task_type in {FileTaskType.RENDER_PAGES, FileTaskType.SPLIT}:
            if not self.output_dir:
                raise ValueError("output_dir_required")
        return self


class FileArtifact(BaseModel):
    """内部产物描述；路径不会进入API序列化结果。"""

    output_path: str = Field(exclude=True, repr=False)
    file_format: str
    mime_type: str
    size_bytes: int = 0
    page_count: int = 0
    paragraph_count: int = 0
    worksheet_count: int = 0
    engine_name: str = ""
    engine_version: str = ""


class FileProcessingResult(BaseModel):
    success: bool
    status: FileProcessingStatus
    artifacts: List[FileArtifact] = Field(default_factory=list)
    text: str = ""
    tables: List[List[List[Optional[str]]]] = Field(default_factory=list)
    page_count: int = 0
    error_type: str = ""
    error_message: str = ""


class ProcessorCapability(BaseModel):
    """服务端可裁决的单项能力声明。"""

    capability_id: str
    processor_name: str
    source_formats: List[str] = Field(default_factory=list)
    target_formats: List[str] = Field(default_factory=list)
    task_types: List[FileTaskType]
    asynchronous: bool
    max_size_bytes: int
    requires_external_binary: bool
    output_mime_types: List[str] = Field(default_factory=list)
    knowledge_base_eligible: bool
    quality_profile: QualityProfile

    @field_validator("source_formats", "target_formats", mode="before")
    @classmethod
    def normalize_formats(cls, values: object) -> List[str]:
        return [
            str(value or "").strip().lower().lstrip(".")
            for value in list(values or [])
        ]


class QualityIssue(BaseModel):
    code: str
    message: str


class QualityCheckResult(BaseModel):
    passed: bool
    artifact: Optional[FileArtifact] = None
    issues: List[QualityIssue] = Field(default_factory=list)
