"""Keep scikit-learn runtime data, but omit its bundled test fixtures."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("sklearn", excludes=["**/tests/**", "**/test/**", "**/examples/**"])
