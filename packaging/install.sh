#!/usr/bin/env bash
set -euo pipefail

# VideoClipper Desktop Installation and Uninstallation Script
# Supports installing to system root (/usr/local) or user home (~/.local).

PREFIX="${PREFIX:-${1:-$HOME/.local}}"
ACTION="${2:-install}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BIN_DIR="${PREFIX}/bin"
LIB_DIR="${PREFIX}/lib/videoclipper"
APP_DIR="${PREFIX}/share/applications"
ICON_SCALABLE_DIR="${PREFIX}/share/icons/hicolor/scalable/apps"
ICON_PNG_DIR="${PREFIX}/share/icons/hicolor/256x256/apps"

install_videoclipper() {
    echo "==> Installing VideoClipper into: ${PREFIX}"
    mkdir -p "${BIN_DIR}" "${LIB_DIR}" "${APP_DIR}" "${ICON_SCALABLE_DIR}" "${ICON_PNG_DIR}"

    # 1. Install library modules
    install -m 644 "${ROOT_DIR}/backend.py" "${LIB_DIR}/backend.py"
    install -m 644 "${ROOT_DIR}/main.py" "${LIB_DIR}/main.py"

    # 2. Install executable launcher
    cat << EOF > "${BIN_DIR}/videoclipper"
#!/usr/bin/env sh
exec /usr/bin/python3 "${LIB_DIR}/main.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/videoclipper"

    # 3. Install desktop launcher
    install -m 644 "${ROOT_DIR}/desktop/videoclipper.desktop" "${APP_DIR}/videoclipper.desktop"

    # 4. Install icons
    install -m 644 "${ROOT_DIR}/assets/videoclipper.svg" "${ICON_SCALABLE_DIR}/videoclipper.svg"
    install -m 644 "${ROOT_DIR}/assets/videoclipper.png" "${ICON_PNG_DIR}/videoclipper.png"

    # Update icon and desktop databases if utilities exist
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q "${PREFIX}/share/icons/hicolor" || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "${APP_DIR}" || true
    fi

    echo "==> VideoClipper successfully installed!"
    echo "    Executable: ${BIN_DIR}/videoclipper"
    echo "    Desktop file: ${APP_DIR}/videoclipper.desktop"
    echo "    Make sure ${BIN_DIR} is in your PATH."
}

uninstall_videoclipper() {
    echo "==> Uninstalling VideoClipper from: ${PREFIX}"
    rm -f "${BIN_DIR}/videoclipper"
    rm -rf "${LIB_DIR}"
    rm -f "${APP_DIR}/videoclipper.desktop"
    rm -f "${ICON_SCALABLE_DIR}/videoclipper.svg"
    rm -f "${ICON_PNG_DIR}/videoclipper.png"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "${APP_DIR}" || true
    fi

    echo "==> VideoClipper successfully uninstalled. User videos and configurations were preserved."
}

case "${ACTION}" in
    install)
        install_videoclipper
        ;;
    uninstall)
        uninstall_videoclipper
        ;;
    *)
        echo "Usage: $0 [PREFIX] [install|uninstall]"
        echo "Default PREFIX is \$HOME/.local"
        exit 1
        ;;
esac
