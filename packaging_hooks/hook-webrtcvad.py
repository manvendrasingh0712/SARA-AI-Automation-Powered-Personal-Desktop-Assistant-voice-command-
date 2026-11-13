"""Compatibility hook for the project's webrtcvad-wheels distribution.

PyInstaller's contrib hook currently asks pip metadata for the distribution
named ``webrtcvad``. This project installs ``webrtcvad-wheels`` instead; the
runtime module does not query distribution metadata, and the native extension
is discovered by normal import analysis.
"""

datas = []
