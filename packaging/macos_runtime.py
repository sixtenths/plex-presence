"""Use bundled public CA roots rather than the build machine's Python certificate path."""
import os
import certifi

os.environ.setdefault('SSL_CERT_FILE', certifi.where())
