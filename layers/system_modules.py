# -*- coding: utf-8 -*-
"""Reviewer-managed system prompt modules stored in the existing users database."""

from datetime import datetime
from threading import RLock
from typing import Dict, Literal, Optional

from pydantic import BaseModel

from layers import auth, organizations


MODULE_TYPES = ("guidance", "tone", "forbidden")
_cache_lock = RLock()
_module_cache = None


class SystemModule(BaseModel):
    module_type: Literal["guidance", "tone", "forbidden"]
    content: str = ""
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None


def init_db() -> None:
    with auth._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_modules (
                module_type TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                updated_by TEXT,
                updated_at TEXT
            )
            """
        )


def list_modules() -> Dict[str, SystemModule]:
    global _module_cache
    with _cache_lock:
        if _module_cache is None:
            with auth._connect() as conn:
                rows = conn.execute(
                    "SELECT module_type, content, updated_by, updated_at FROM system_modules"
                ).fetchall()
            stored = {
                str(row["module_type"]): SystemModule(**dict(row))
                for row in rows
                if str(row["module_type"]) in MODULE_TYPES
            }
            _module_cache = {
                module_type: stored.get(
                    module_type, SystemModule(module_type=module_type)
                )
                for module_type in MODULE_TYPES
            }
        result = {
            name: module.model_copy(deep=True)
            for name, module in _module_cache.items()
        }
    result["guidance"].content = organizations.generate_guidance_content()
    return result


def save_modules(contents: Dict[str, str], updated_by: str) -> Dict[str, SystemModule]:
    """保存tone/forbidden模块；guidance已改为按组织自动生成，不再接受手动传值。"""
    global _module_cache
    if "guidance" in contents:
        raise ValueError("guidance模块已改为按组织自动生成，请通过组织管理接口调整")
    invalid = set(contents) - set(MODULE_TYPES)
    if invalid:
        raise ValueError("不支持的系统模块类型")
    updated_at = datetime.now().isoformat()
    with auth._connect() as conn:
        for module_type in MODULE_TYPES:
            if module_type not in contents:
                continue
            conn.execute(
                """
                INSERT INTO system_modules (module_type, content, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(module_type) DO UPDATE SET
                    content = excluded.content,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (module_type, str(contents[module_type] or ""), updated_by, updated_at),
            )
    with _cache_lock:
        _module_cache = None
    return list_modules()


def prompt_prefix(original_prompt: str, include_forbidden: bool = True) -> str:
    modules = list_modules()
    labels = [("guidance", "规范模块"), ("tone", "语气风格模块")]
    if include_forbidden:
        labels.append(("forbidden", "禁用模块"))
    parts = []
    for module_type, label in labels:
        content = modules[module_type].content.strip()
        if content:
            parts.append("%s：\n%s" % (label, content))
    if original_prompt:
        parts.append(original_prompt)
    return "\n\n".join(parts)


init_db()
