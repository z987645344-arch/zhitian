# -*- coding: utf-8 -*-
"""文件处理器层间传递的结构化模型。"""

from enum import Enum
from typing import Callable, List, Optional, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator


class FileTaskType(str, Enum):
    EXTRACT = "extract"
    EDIT = "edit"  # 仅定义；没有适配器登记时必须拒绝。
    WRITE_TEXT = "write_text"
    CONVERT = "convert"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_TABLES = "extract_tables"
    RENDER_PAGES = "render_pages"
    MERGE = "merge"
    SPLIT = "split"


class FileEntry(str, Enum):
    UPLOAD_AUTO = "upload_auto"
    APP_MANUAL = "app_manual"
    AGENT_CHAT = "agent_chat"


class FileTaskKind(str, Enum):
    EXTRACT = "extract"
    CONVERT = "convert"
    EDIT = "edit"


class EngineType(str, Enum):
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class EngineStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class EngineState(BaseModel):
    engine_name: str
    status: EngineStatus = EngineStatus.PENDING
    reason: str = "not_checked"
    last_checked_at: Optional[str] = None
    elapsed_seconds: Optional[float] = None


class EngineProbeResult(BaseModel):
    success: bool
    reason: str = ""


class ResourceBudget(BaseModel):
    """与文件后缀无关；多媒体字段仅预留，不表示已经具备对应能力。"""
    engine_type: EngineType = EngineType.DOCUMENT
    max_input_bytes: int = Field(default=0, ge=0)
    max_output_bytes: int = Field(default=0, ge=0)
    max_pages: int = Field(default=0, ge=0)
    max_pixels: int = Field(default=0, ge=0)
    max_execution_seconds: float = Field(default=0, ge=0)
    max_media_duration_seconds: Optional[float] = Field(default=None, ge=0)
    max_width: Optional[int] = Field(default=None, ge=0)
    max_height: Optional[int] = Field(default=None, ge=0)
    max_bitrate: Optional[int] = Field(default=None, ge=0)


class FileTaskSpec(BaseModel):
    entry: FileEntry
    task_type: FileTaskKind
    source_format: str
    target_format: str = ""
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    encrypted: bool = False


class FileTaskProgress(BaseModel):
    stage: str
    processed: int = Field(default=0, ge=0)
    total: Optional[int] = Field(default=None, ge=0)


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


ProgressCallback = Callable[[FileTaskProgress], None]


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
    entry: FileEntry = FileEntry.APP_MANUAL
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    encrypted: bool = False
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
        # 兼容既有调用字段：新预算只收紧，不得绕过原有上限。
        for legacy, budget in (("max_input_size_bytes", "max_input_bytes"),
                               ("max_output_size_bytes", "max_output_bytes"),
                               ("max_pages", "max_pages")):
            values = [value for value in (getattr(self, legacy), getattr(self.resource_budget, budget)) if value > 0]
            if values:
                setattr(self, legacy, min(values))
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
    entries: List[FileEntry] = Field(default_factory=lambda: list(FileEntry))
    engine_type: EngineType = EngineType.DOCUMENT

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
