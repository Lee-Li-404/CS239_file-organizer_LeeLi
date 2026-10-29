"""Explicit local anonymization tool; no network or model calls.

Handles bounded PDF/text excerpts, labeled/configured names and credentials,
and an optional local name detector. No remote detector is used. It is not a
complete PII detector. Keep the instance in the runtime, outside agent control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TEXT_SUFFIXES = {".txt", ".md", ".log", ".py", ".bib", ".tex", ".csv"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".pdf"}


TOOL_SCHEMA = {
    "name": "anonymize_file",
    "description": (
        "Create a local anonymized text excerpt from PDF or UTF-8 text/code. Returns an "
        "artifact ID and output path, never the original text. Upload the "
        "output instead of the original file."
    ),
    "parameters": {
        "type": "object",
    "properties": {"path": {"type": "string"},
                   "max_chars": {"type": "integer", "minimum": 512, "maximum": 12000},
                   "max_pages": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["path"],
        "additionalProperties": False,
    },
}

_FIELD = re.compile(
    r"^(?P<prefix>[ \t]*(?:[-*][ \t]+)?"
    r"(?P<label>full[ _-]?name|name|姓名|名字|"
    r"password|passwd|pwd|密码|"
    r"api[ _-]?key|access[ _-]?token|secret[ _-]?key|API密钥)"
    r"[ \t]*[:=：][ \t]*)(?P<value>[^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
_INLINE_SECRET = re.compile(
    r"\b(?P<label>password|passwd|pwd|api[ _-]?key|access[ _-]?token|secret[ _-]?key)"
    r"[ \t]*[:=][ \t]*(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)",
    re.IGNORECASE,
)
_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_EMAIL = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NAME_LABELS = {"fullname", "name", "姓名", "名字"}
_PASSWORD_LABELS = {"password", "passwd", "pwd", "密码"}


def _label(value: str) -> str:
    return re.sub(r"[ _-]", "", value.casefold())


def _value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


@dataclass(frozen=True)
class _Artifact:
    path: Path
    digest: str


class LocalAnonymizer:
    """One trusted runtime instance; the model receives only the tool schema."""

    def __init__(self, workspace: str | Path, known_names: Iterable[str] = ()):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        names = dict.fromkeys(name.strip() for name in known_names if name.strip())
        self._names = {name: f"[PERSON_{i:03d}]"
                       for i, name in enumerate(names, 1)}
        self._artifacts: dict[str, _Artifact] = {}

    def _inside(self, path: str | Path) -> Path:
        candidate = self.workspace / path
        try:
            relative = candidate.relative_to(self.workspace)
        except ValueError:
            raise ValueError("Path is outside the workspace.") from None
        current = self.workspace
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symbolic links are not supported.")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.workspace):
            raise ValueError("Path is outside the workspace.")
        return resolved

    def _redact(self, text: str) -> str:
        # Collect first, then replace every exact occurrence, including repeats
        # in prose. Secret values never enter artifact metadata or return values.
        replacements: dict[str, str] = {}
        detector = os.environ.get("LOCAL_NAME_DETECTOR", "")
        if detector:
            # A trusted locally configured binary, not a command selected by
            # the model. Raw input and detected names stay inside this process.
            result = subprocess.run([detector], input=json.dumps({"text": text}),
                                    capture_output=True, text=True, timeout=15,
                                    check=True, env={"PATH": os.defpath, "LANG": "C"})
            found = json.loads(result.stdout)
            if not isinstance(found, list) or not all(isinstance(v, str) for v in found):
                raise ValueError("Local name detector returned an invalid result.")
            for value in found:
                if value and value not in self._names:
                    self._names[value] = f"[PERSON_{len(self._names) + 1:03d}]"
        for match in _FIELD.finditer(text):
            label, value = _label(match["label"]), _value(match["value"])
            if not value:
                continue
            if label in _NAME_LABELS:
                if value not in self._names:
                    self._names[value] = f"[PERSON_{len(self._names) + 1:03d}]"
                replacements.setdefault(value, self._names[value])
            else:
                replacements[value] = (
                    "[REDACTED_PASSWORD]" if label in _PASSWORD_LABELS
                    else "[REDACTED_API_KEY]"
                )
        for match in _INLINE_SECRET.finditer(text):
            value = _value(match["value"])
            if value:
                replacements[value] = (
                    "[REDACTED_PASSWORD]"
                    if _label(match["label"]) in _PASSWORD_LABELS
                    else "[REDACTED_API_KEY]"
                )
        for match in _KEY.finditer(text):
            replacements[match[0]] = "[REDACTED_API_KEY]"
        for match in _EMAIL.finditer(text):
            replacements[match[0]] = "[REDACTED_EMAIL]"
        for name, token in self._names.items():
            replacements.setdefault(name, token)
        if not replacements:
            return text
        # A single substitution avoids reprocessing inserted placeholders.
        pattern = re.compile("|".join(re.escape(v) for v in
                                      sorted(replacements, key=len, reverse=True)))
        return pattern.sub(lambda m: replacements[m[0]], text)

    def anonymize_file(self, path: str, max_chars: int = 6000, max_pages: int = 2) -> dict:
        if not isinstance(max_chars, int) or not 512 <= max_chars <= 12000:
            raise ValueError("max_chars must be between 512 and 12000.")
        if not isinstance(max_pages, int) or not 1 <= max_pages <= 5:
            raise ValueError("max_pages must be between 1 and 5.")
        source = self._inside(path)
        if source.suffix.casefold() not in SUPPORTED_SUFFIXES:
            raise ValueError("Unsupported source format.")
        if not source.is_file():
            raise ValueError("Source must be a regular file.")
        # Bound local work; this is not a promise of complete secret detection.
        is_pdf = source.suffix.casefold() == ".pdf"
        limit = 32_000_000 if is_pdf else 2_000_000
        if source.stat().st_size > limit:
            raise ValueError("Source exceeds the format's size limit.")
        if is_pdf:
            try:
                from pypdf import PdfReader
                # Suppress repetitive repair warnings, not extraction errors.
                # A failed/empty parse still returns no artifact.
                logging.getLogger("pypdf").setLevel(logging.ERROR)
                reader = PdfReader(source)
                if reader.is_encrypted:
                    raise ValueError("Encrypted PDF is not supported.")
                parts = []
                for page in reader.pages[:max_pages]:
                    contents = page.get_contents()
                    if contents and len(contents.get_data()) > 12_000_000:
                        raise ValueError("PDF page content stream is too large.")
                    parts.append(page.extract_text() or "")
                    if sum(len(v) for v in parts) >= max_chars:
                        break
                text = "\n".join(parts)
                if not text.strip():
                    raise ValueError("PDF has no extractable text; OCR not enabled.")
            except Exception:
                raise ValueError("Local PDF extraction failed; no output returned.") from None
        else:
            raw = source.read_bytes()
            if len(raw) > limit:
                raise ValueError("Source exceeds the format's size limit.")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("Source must be UTF-8 text.") from None
        sanitized = self._redact(text[:max_chars]).encode("utf-8")
        directory = self.workspace / ".anonymized"
        if directory.is_symlink():
            raise ValueError("Output directory must not be a symbolic link.")
        directory.mkdir(mode=0o700, exist_ok=True)
        artifact_id = "anon_" + uuid.uuid4().hex
        output = directory / (artifact_id + ".txt")
        with output.open("xb") as stream:
            stream.write(sanitized)
        output.chmod(0o600)
        self._artifacts[artifact_id] = _Artifact(
            output, hashlib.sha256(sanitized).hexdigest()
        )
        return {
            "artifact_id": artifact_id,
            "output_file": output.relative_to(self.workspace).as_posix(),
            "status": "anonymized",
            "excerpt_only": is_pdf or len(text) > max_chars,
            "max_chars": max_chars,
            "local_name_detector": bool(os.environ.get("LOCAL_NAME_DETECTOR")),
        }

    def resolve_output(self, artifact_id: str) -> Path:
        """Local helper for a future upload guard; performs no upload.

        This is not a completed Phase 2 framework integration or an atomic
        read-and-upload implementation. The registry lives only in this instance.
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise ValueError("Unknown anonymizer artifact.")
        path = self._inside(artifact.path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.digest:
            raise ValueError("Anonymizer output has changed.")
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Source path relative to the workspace")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--names-file", type=Path,
                        help="Local UTF-8 list of known names, one per line")
    args = parser.parse_args()
    try:
        names = args.names_file.read_text(encoding="utf-8").splitlines() if args.names_file else []
        result = LocalAnonymizer(args.workspace, names).anonymize_file(args.path)
    except (ValueError, OSError):
        # Do not echo original paths, content or decoding buffers to the agent.
        parser.exit(1, "Local anonymization failed; no artifact was returned.\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
