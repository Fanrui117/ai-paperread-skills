"""配置加载：config/settings.toml > 环境变量覆盖 > 样例默认值。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_zotero_dir() -> Path:
    """猜测 Zotero 数据目录：环境变量 > 常见路径。"""
    env = os.environ.get("ZOTERO_DATA_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    candidates = [
        home / "Zotero",
        home / "Documents" / "Zotero",
        Path("D:/Software/Zotero"),
    ]
    for p in candidates:
        if (p / "zotero.sqlite").is_file():
            return p
    return home / "Zotero"


def _as_path(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    return p


@dataclass
class Config:
    vault_root: Path
    library_subdir: str = ""
    zotero_data_dir: Path = field(default_factory=default_zotero_dir)
    local_api: str = "http://127.0.0.1:23119/api"
    prefer_local_api: bool = False
    translate_provider: str = "agent"
    translate_api_base: str = "https://api.deepseek.com"
    translate_model: str = "deepseek-chat"
    translate_api_key: str = ""
    translate_api_key_env: str = "DEEPSEEK_API_KEY"
    use_dsh_credentials: bool = False
    translate_batch_chars: int = 4000
    glossary_path: Path | None = None
    note_language: str = "zh"
    note_write_tags: bool = True
    names: dict[str, str] = field(default_factory=dict)
    ignore_collections: list[str] = field(default_factory=list)
    ignore_items: list[str] = field(default_factory=list)
    baidu_appid: str = ""
    baidu_secret: str = ""
    post_replace: dict[str, str] = field(default_factory=dict)
    mineru_api_key: str = ""
    mineru_api_base: str = "https://mineru.net/api/v4"
    extract_backend: str = "pymupdf"
    image_grid: bool = True
    source_path: Path | None = None

    # ---- 派生路径 ----
    @property
    def library_root(self) -> Path:
        sub = self.library_subdir.strip().strip("/\\")
        return self.vault_root / sub if sub else self.vault_root

    @property
    def state_dir(self) -> Path:
        return self.vault_root / ".paperread"

    @property
    def library_json(self) -> Path:
        return self.state_dir / "library.json"

    @property
    def plan_json(self) -> Path:
        return self.state_dir / "plan.json"

    @property
    def state_json(self) -> Path:
        return self.state_dir / "state.json"

    def api_key(self) -> str:
        if self.translate_api_key:
            return self.translate_api_key
        env = os.environ.get(self.translate_api_key_env, "")
        if env:
            return env
        if self.use_dsh_credentials:
            try:
                import yaml

                path = Path.home() / ".dsh" / ".credentials.yaml"
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                return str((data.get("refs") or {}).get(self.translate_api_key_env, "") or "")
            except Exception:
                return ""
        return ""

    def ensure_dirs(self) -> None:
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)


def _candidate_configs(explicit: str | Path | None) -> list[Path]:
    out: list[Path] = []
    if explicit:
        out.append(Path(explicit))
    env = os.environ.get("PAPERREAD_CONFIG")
    if env:
        out.append(Path(env))
    out.append(PROJECT_ROOT / "config" / "settings.toml")
    out.append(PROJECT_ROOT / "config" / "settings.example.toml")
    return out


def load_config(explicit: str | Path | None = None) -> Config:
    path = next((p for p in _candidate_configs(explicit) if p.is_file()), None)
    raw: dict = {}
    if path is not None:
        with open(path, "rb") as f:
            raw = tomllib.load(f)

    vault = raw.get("vault", {})
    zot = raw.get("zotero", {})
    tr = raw.get("translate", {})
    note = raw.get("note", {})
    ign = raw.get("ignore", {})

    vault_root = os.environ.get("PAPERREAD_VAULT") or vault.get("root") or str(Path.home() / "PaperNotes")
    glossary = tr.get("glossary")
    cfg = Config(
        vault_root=Path(vault_root).expanduser(),
        library_subdir=str(vault.get("library_subdir", "")),
        zotero_data_dir=Path(zot.get("data_dir") or default_zotero_dir()).expanduser(),
        local_api=str(zot.get("local_api", "http://127.0.0.1:23119/api")),
        prefer_local_api=bool(zot.get("prefer_local_api", False)),
        translate_provider=str(tr.get("provider", "agent")),
        translate_api_base=str(tr.get("api_base", "https://api.deepseek.com")),
        translate_model=str(tr.get("model", "deepseek-chat")),
        translate_api_key=str(tr.get("api_key", "") or ""),
        translate_api_key_env=str(tr.get("api_key_env", "DEEPSEEK_API_KEY")),
        use_dsh_credentials=bool(tr.get("use_dsh_credentials", False)),
        translate_batch_chars=int(tr.get("batch_chars", 4000)),
        glossary_path=(PROJECT_ROOT / glossary) if glossary else None,
        note_language=str(note.get("language", "zh")),
        note_write_tags=bool(note.get("write_tags", True)),
        names={str(k): str(v) for k, v in raw.get("names", {}).items()},
        ignore_collections=[str(x) for x in ign.get("collections", [])],
        ignore_items=[str(x) for x in ign.get("items", [])],
        baidu_appid=str(tr.get("baidu_appid", "")),
        baidu_secret=str(tr.get("baidu_secret", "")),
        post_replace={str(k): str(v) for k, v in tr.get("post_replace", {}).items()},
        mineru_api_key=str((raw.get("mineru", {}) or {}).get("api_key", "")),
        mineru_api_base=str((raw.get("mineru", {}) or {}).get("api_base", "https://mineru.net/api/v4")),
        extract_backend=str((raw.get("extract", {}) or {}).get("backend", "pymupdf")),
        image_grid=bool((raw.get("render", {}) or {}).get("image_grid", True)),
        source_path=path,
    )
    return cfg
