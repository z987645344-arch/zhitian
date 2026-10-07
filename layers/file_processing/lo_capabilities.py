"""LibreOffice唯一格式白名单；不依赖业务配置。"""

LIBREOFFICE_SOURCES = {
    "pdf": ["doc", "docx", "xls", "xlsx", "ppt", "pptx", "md", "txt"],
    "docx": ["doc", "md", "txt"],
}
