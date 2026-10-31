# Attribution

Plex Presence is inspired by [phin05/discord-rich-presence-plex](https://github.com/phin05/discord-rich-presence-plex), copyright phin05 and contributors, licensed under GNU AGPL version 3. This project uses an independent Python implementation and an experimental OAuth presence publisher instead of the upstream Discord desktop RPC connection.

Plex Presence is distributed under GNU AGPL version 3; see LICENSE. It is an independent community project and is not affiliated with or endorsed by Plex or Discord. Plex, Discord, and media artwork belong to their respective owners.

Native downloads bundle Python and PyInstaller runtime components, which retain their own licenses. PyInstaller's bootloader exception permits distribution of bundled applications. Python license files supplied by the packager remain in the application bundle.

The native desktop interface uses Qt and Qt for Python (PySide6-Essentials and Shiboken6), version 6.7.3, under the GNU LGPL version 3. License copies are included in THIRD_PARTY_LICENSES. The corresponding source is available from [Qt Base v6.7.3](https://github.com/qt/qtbase/tree/v6.7.3) and [Qt for Python v6.7.3](https://github.com/pyside/pyside-setup/tree/v6.7.3). Bundled Qt libraries remain separate shared-library files and may be replaced with compatible modified versions. Reverse engineering for debugging modifications to LGPL-covered components is permitted by their license.

The Plex Presence app icon is an original pair of overlapping message panels. It does not use the Plex logo.

Mac downloads include [Certifi](https://github.com/certifi/python-certifi) and its Mozilla-derived public certificate roots under MPL 2.0. Its license and package metadata are included in the bundled certifi distribution metadata. This lets the app verify HTTPS connections without depending on a Python installation on the user's Mac.
