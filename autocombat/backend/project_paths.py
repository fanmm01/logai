"""Paths shared by the checkout, release bundle and isolated task snapshots."""
import os
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_ROOT.parent if SOURCE_ROOT.name == 'backend' else SOURCE_ROOT


def runtime_dir(source_root=None):
    source_root = Path(source_root).resolve() if source_root else SOURCE_ROOT
    if source_root == SOURCE_ROOT and SOURCE_ROOT.name == 'backend':
        override = os.environ.get('AUTOCOMBAT_RUNTIME_DIR')
        return Path(override).resolve() if override else PROJECT_ROOT / '.ui-runtime'
    return source_root / '.ui-runtime'
