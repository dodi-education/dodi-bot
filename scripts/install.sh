#!/bin/bash
# DODI Installation Script
# One-click setup for Raspberry Pi 4 with ReSpeaker 2-Mics Pi HAT
#
# Usage:
#   sudo ./install.sh                                    # Interactive mode
#   sudo ./install.sh --provider gemini --api-key KEY    # Non-interactive
#   sudo ./install.sh --provider openai                  # Partial (prompts for key)
#   sudo ./install.sh --help                             # Show help
#
# Parameters:
#   --provider, -p    AI provider: gemini, openai, grok, skip
#   --api-key, -k     API key for the selected provider
#   --language, -l    Language code (default: en-US)
#   --skip-models     Skip downloading local AI models
#   --help, -h        Show this help message

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Model URLs
VOSK_MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip"
VOSK_MODEL_NAME="vosk-model-small-de-0.15"
# Piper binary (standalone, includes phonemizer - works on ARM64)
PIPER_BINARY_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz"
PIPER_BINARY_NAME="piper"
# Piper voices are hosted on Hugging Face
# English voice
PIPER_VOICE_EN_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
PIPER_VOICE_EN_JSON_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
PIPER_VOICE_EN_NAME="en_US-lessac-medium"
# German voice (Thorsten Medium - natural sounding)
PIPER_VOICE_DE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx"
PIPER_VOICE_DE_JSON_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json"
PIPER_VOICE_DE_NAME="de_DE-thorsten-medium"

# Default values
PROVIDER=""
API_KEY=""
LANGUAGE="en-US"
SKIP_MODELS=false
NEED_REBOOT=false

# ============================================
# Helper Functions
# ============================================

show_help() {
    echo "DODI Installation Script"
    echo ""
    echo "Usage:"
    echo "  sudo ./install.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -p, --provider PROVIDER   AI provider: gemini, openai, grok, skip"
    echo "  -k, --api-key KEY         API key for the selected provider"
    echo "  -l, --language LANG       Language code (default: en-US)"
    echo "      --skip-models         Skip downloading local AI models"
    echo "  -h, --help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  sudo ./install.sh                                  # Interactive"
    echo "  sudo ./install.sh -p gemini -k YOUR_API_KEY        # Fully automated"
    echo "  sudo ./install.sh --provider openai                # Prompts for API key"
    echo "  sudo ./install.sh -p skip                          # Skip API setup"
    echo ""
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -p|--provider)
                PROVIDER="$2"
                shift 2
                ;;
            -k|--api-key)
                API_KEY="$2"
                shift 2
                ;;
            -l|--language)
                LANGUAGE="$2"
                shift 2
                ;;
            --skip-models)
                SKIP_MODELS=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo -e "${RED}[ERROR] Unknown option: $1${NC}"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
}

validate_provider() {
    local provider="$1"
    case "$provider" in
        gemini|openai|grok|skip)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

prompt_provider() {
    echo ""
    echo -e "${CYAN}Which AI provider will you use?${NC}"
    echo "  1) Gemini  (Google - free tier available)"
    echo "  2) OpenAI  (ChatGPT)"
    echo "  3) Grok    (xAI)"
    echo "  4) Skip    (configure later)"
    echo ""

    while true; do
        read -p "Enter choice [1-4]: " choice
        case $choice in
            1) PROVIDER="gemini"; break ;;
            2) PROVIDER="openai"; break ;;
            3) PROVIDER="grok"; break ;;
            4) PROVIDER="skip"; break ;;
            *) echo -e "${YELLOW}Invalid choice. Please enter 1-4.${NC}" ;;
        esac
    done
}

prompt_api_key() {
    local provider="$1"
    echo ""
    echo -e "${CYAN}Enter your ${provider^} API key:${NC}"
    echo -e "${YELLOW}(The key will be hidden as you type)${NC}"

    read -s -p "API Key: " API_KEY
    echo ""

    if [ -z "$API_KEY" ]; then
        echo -e "${YELLOW}[!] No API key entered. You can add it later in .env${NC}"
    fi
}

get_env_var_name() {
    local provider="$1"
    case "$provider" in
        gemini) echo "GEMINI_API_KEY" ;;
        openai) echo "OPENAI_API_KEY" ;;
        grok)   echo "GROK_API_KEY" ;;
    esac
}

write_env_file() {
    local provider="$1"
    local api_key="$2"
    local env_file="$PROJECT_DIR/.env"

    # Start with template
    cat > "$env_file" << 'EOF'
# DODI Environment Variables
# API keys for AI providers (only need the one you're using)

EOF

    # Write all keys, with the active one filled in
    if [ "$provider" = "gemini" ] && [ -n "$api_key" ]; then
        echo "GEMINI_API_KEY=$api_key" >> "$env_file"
    else
        echo "GEMINI_API_KEY=" >> "$env_file"
    fi

    if [ "$provider" = "openai" ] && [ -n "$api_key" ]; then
        echo "OPENAI_API_KEY=$api_key" >> "$env_file"
    else
        echo "OPENAI_API_KEY=" >> "$env_file"
    fi

    if [ "$provider" = "grok" ] && [ -n "$api_key" ]; then
        echo "GROK_API_KEY=$api_key" >> "$env_file"
    else
        echo "GROK_API_KEY=" >> "$env_file"
    fi

    chmod 600 "$env_file"  # Restrict permissions for security
}

update_settings_provider() {
    local provider="$1"
    local settings_file="$PROJECT_DIR/config/settings.json"

    if [ -f "$settings_file" ] && [ "$provider" != "skip" ]; then
        # Use sed to update the active provider
        sed -i "s/\"active\": \"[^\"]*\"/\"active\": \"$provider\"/" "$settings_file"
    fi
}

# ============================================
# Main Script
# ============================================

# Parse command line arguments
parse_args "$@"

echo -e "${BLUE}"
echo "=================================================="
echo "  DODI Installation Script"
echo "  Didactic Doing - Robot Companion for Kids"
echo "=================================================="
echo -e "${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run as root: sudo ./install.sh${NC}"
    exit 1
fi

# Get the actual user (not root)
ACTUAL_USER="${SUDO_USER:-$USER}"
ACTUAL_HOME=$(eval echo ~$ACTUAL_USER)

echo -e "${GREEN}[INFO]${NC} Installing for user: $ACTUAL_USER"
echo -e "${GREEN}[INFO]${NC} Project directory: $PROJECT_DIR"

# ============================================
# Interactive prompts for missing parameters
# ============================================

# Prompt for provider if not specified
if [ -z "$PROVIDER" ]; then
    prompt_provider
elif ! validate_provider "$PROVIDER"; then
    echo -e "${RED}[ERROR] Invalid provider: $PROVIDER${NC}"
    echo "Valid options: gemini, openai, grok, skip"
    exit 1
fi

# Prompt for API key if provider selected but no key given
if [ "$PROVIDER" != "skip" ] && [ -z "$API_KEY" ]; then
    prompt_api_key "$PROVIDER"
fi

echo ""
echo -e "${GREEN}[INFO]${NC} Configuration:"
echo -e "  Provider: ${CYAN}$PROVIDER${NC}"
echo -e "  Language: ${CYAN}$LANGUAGE${NC}"
if [ -n "$API_KEY" ]; then
    echo -e "  API Key:  ${CYAN}(provided)${NC}"
else
    echo -e "  API Key:  ${YELLOW}(not set)${NC}"
fi
echo ""

# ============================================
# Step 1: System Update
# ============================================
echo -e "${BLUE}[1/7] Updating system packages...${NC}"

# Check if apt cache was updated recently (within last hour)
APT_CACHE_AGE=3600  # 1 hour in seconds
APT_LISTS_DIR="/var/lib/apt/lists"
if [ -d "$APT_LISTS_DIR" ] && [ "$(find "$APT_LISTS_DIR" -maxdepth 1 -type f -mmin -60 | head -1)" ]; then
    echo -e "${GREEN}[OK]${NC} System packages already up to date (updated recently)"
else
    apt-get update -qq
    echo -e "${GREEN}[OK]${NC} System updated"
fi
echo ""

# ============================================
# Step 2: Install System Dependencies
# ============================================
echo -e "${BLUE}[2/7] Installing system dependencies...${NC}"

PACKAGES=(
    "git"
    "python3-venv"
    "python3-pip"
    "python3-dev"
    "libasound2-dev"
    "portaudio19-dev"
    "libopenblas-dev"
    "libsndfile1"
    "wget"
    "unzip"
    "dkms"
    "espeak-ng"
    "libespeak-ng-dev"
)

# Kernel headers (needed for seeed-voicecard driver compilation)
install_kernel_headers() {
    echo -e "  [~] Checking kernel headers..."

    local kernel_version=$(uname -r)

    # Check if headers are already installed
    if [ -d "/usr/src/linux-headers-$kernel_version" ] || [ -d "/lib/modules/$kernel_version/build" ]; then
        echo -e "  [+] Kernel headers already installed"
        return 0
    fi

    # Try various package names used by different Raspberry Pi OS versions
    local header_packages=(
        "linux-headers-$kernel_version"
        "raspberrypi-kernel-headers"
        "linux-headers-rpi-v8"
        "linux-headers-arm64"
        "linux-headers-aarch64"
    )

    for pkg in "${header_packages[@]}"; do
        if apt-cache show "$pkg" &>/dev/null 2>&1; then
            echo -e "  [~] Installing $pkg..."
            if apt-get install -y -qq "$pkg" 2>/dev/null; then
                echo -e "  [+] Kernel headers installed"
                return 0
            fi
        fi
    done

    echo -e "${YELLOW}  [!] Could not find kernel headers package${NC}"
    echo -e "${YELLOW}      Driver overlay will be installed without compilation${NC}"
    return 1
}

# Check if all packages are already installed
MISSING_PACKAGES=()
for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
    echo -e "${GREEN}[OK]${NC} All system dependencies already installed"
else
    echo -e "  [~] Installing ${#MISSING_PACKAGES[@]} missing packages..."
    for pkg in "${MISSING_PACKAGES[@]}"; do
        echo -e "  [~] Installing $pkg..."
        apt-get install -y -qq "$pkg"
        echo -e "  [+] $pkg (installed)"
    done
    echo -e "${GREEN}[OK]${NC} System dependencies installed"
fi
echo ""

# ============================================
# Step 3: Detect and Install Seeed Voice Card
# ============================================
echo -e "${BLUE}[3/7] Checking Seeed Voice Card driver...${NC}"

SEEED_INSTALLED=false
SEEED_WORKING=false

# Determine boot config location (changed in newer Pi OS)
if [ -d "/boot/firmware" ]; then
    BOOT_DIR="/boot/firmware"
else
    BOOT_DIR="/boot"
fi
OVERLAY_DIR="$BOOT_DIR/overlays"
CONFIG_FILE="$BOOT_DIR/config.txt"

# Get kernel version for selecting the correct driver branch
# HinTak's fork provides kernel-specific branches (v6.6, v6.12, etc.)
# See: https://github.com/respeaker/seeed-voicecard/issues/346
get_kernel_branch() {
    local kernel_version=$(uname -r)
    # Extract major.minor version (e.g., "6.12" from "6.12.62+rpt-rpi-v8")
    local major_minor=$(echo "$kernel_version" | grep -oE '^[0-9]+\.[0-9]+')
    echo "v$major_minor"
}

KERNEL_BRANCH=$(get_kernel_branch)
echo -e "  [~] Detected kernel branch: ${CYAN}$KERNEL_BRANCH${NC}"

# Check if driver is loaded and working
if arecord -l 2>/dev/null | grep -qi "seeed\|respeaker"; then
    SEEED_INSTALLED=true
    SEEED_WORKING=true
    echo -e "${GREEN}[OK]${NC} Seeed Voice Card driver installed and working"
# Check if overlay and config are set up (needs reboot to activate)
elif [ -f "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" ] && grep -q "dtoverlay=seeed-2mic-voicecard" "$CONFIG_FILE" 2>/dev/null; then
    SEEED_INSTALLED=true
    echo -e "${GREEN}[OK]${NC} Seeed Voice Card driver installed (reboot to activate)"
    NEED_REBOOT=true
fi

if [ "$SEEED_INSTALLED" = false ]; then
    echo -e "${YELLOW}[~]${NC} Seeed Voice Card driver not found, installing..."

    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"

    INSTALL_SUCCESS=false

    # Method 1: Use HinTak's fork with kernel-specific branch (recommended)
    # This fork is actively maintained and has branches matching kernel versions
    echo "  Cloning HinTak's seeed-voicecard (branch $KERNEL_BRANCH)..."
    if git clone --depth 1 -b "$KERNEL_BRANCH" https://github.com/HinTak/seeed-voicecard.git 2>/dev/null; then
        cd seeed-voicecard

        # Check if kernel headers are available for compilation
        install_kernel_headers
        HEADERS_AVAILABLE=$?

        if [ $HEADERS_AVAILABLE -eq 0 ]; then
            # Try the installer from HinTak's fork
            echo "  Running seeed-voicecard installer..."
            if timeout 120 ./install.sh --compat-kernel > /tmp/seeed_install.log 2>&1; then
                INSTALL_SUCCESS=true
                echo -e "  ${GREEN}[+]${NC} Driver installed via HinTak's installer (branch $KERNEL_BRANCH)"
            fi
        fi

        # If installer failed, try manual overlay copy
        if [ "$INSTALL_SUCCESS" = false ]; then
            echo "  Installer failed, trying manual overlay installation..."
            if [ -f "seeed-2mic-voicecard.dtbo" ]; then
                cp seeed-2mic-voicecard.dtbo "$OVERLAY_DIR/"
                echo -e "  ${GREEN}[+]${NC} Copied overlay from HinTak's fork (branch $KERNEL_BRANCH)"
                INSTALL_SUCCESS=true
            elif [ -d "overlays" ] && [ -f "overlays/seeed-2mic-voicecard.dtbo" ]; then
                cp overlays/seeed-2mic-voicecard.dtbo "$OVERLAY_DIR/"
                echo -e "  ${GREEN}[+]${NC} Copied overlay from HinTak's fork (branch $KERNEL_BRANCH)"
                INSTALL_SUCCESS=true
            fi
        fi

        # Copy ALSA configuration files
        if [ -d "etc/voicecard" ]; then
            mkdir -p /etc/voicecard
            cp -r etc/voicecard/* /etc/voicecard/ 2>/dev/null || true
        fi

        # Copy asound configuration
        if [ -f "asound_2mic.conf" ]; then
            cp asound_2mic.conf /etc/voicecard/ 2>/dev/null || true
        fi

        cd "$TEMP_DIR"
    else
        echo -e "${YELLOW}  [!] Branch $KERNEL_BRANCH not found in HinTak's fork${NC}"
    fi

    # Method 2: Fallback to official respeaker repo (for older kernels)
    if [ "$INSTALL_SUCCESS" = false ]; then
        echo "  Trying official respeaker repository..."
        rm -rf seeed-voicecard 2>/dev/null
        if git clone --depth 1 https://github.com/respeaker/seeed-voicecard.git 2>/dev/null; then
            cd seeed-voicecard

            install_kernel_headers
            HEADERS_AVAILABLE=$?

            if [ $HEADERS_AVAILABLE -eq 0 ]; then
                echo "  Running official seeed-voicecard installer..."
                if timeout 120 ./install.sh --compat-kernel > /tmp/seeed_install.log 2>&1; then
                    INSTALL_SUCCESS=true
                    echo -e "  ${GREEN}[+]${NC} Driver installed via official installer"
                fi
            fi

            # Try manual overlay copy
            if [ "$INSTALL_SUCCESS" = false ]; then
                if [ -f "seeed-2mic-voicecard.dtbo" ]; then
                    cp seeed-2mic-voicecard.dtbo "$OVERLAY_DIR/"
                    echo -e "  ${GREEN}[+]${NC} Copied overlay from official repository"
                    INSTALL_SUCCESS=true
                elif [ -d "overlays" ] && [ -f "overlays/seeed-2mic-voicecard.dtbo" ]; then
                    cp overlays/seeed-2mic-voicecard.dtbo "$OVERLAY_DIR/"
                    echo -e "  ${GREEN}[+]${NC} Copied overlay from official repository"
                    INSTALL_SUCCESS=true
                fi

                # Copy ALSA configuration files
                if [ -d "etc/voicecard" ]; then
                    mkdir -p /etc/voicecard
                    cp -r etc/voicecard/* /etc/voicecard/ 2>/dev/null || true
                fi

                if [ -f "asound_2mic.conf" ]; then
                    cp asound_2mic.conf /etc/voicecard/ 2>/dev/null || true
                fi
            fi

            cd "$TEMP_DIR"
        fi
    fi

    # Method 3: Download pre-built overlay directly from HinTak's kernel branch
    if [ "$INSTALL_SUCCESS" = false ]; then
        echo "  Trying to download pre-built overlay from HinTak's $KERNEL_BRANCH branch..."
        OVERLAY_URL="https://github.com/HinTak/seeed-voicecard/raw/$KERNEL_BRANCH/seeed-2mic-voicecard.dtbo"
        if wget -q --timeout=30 "$OVERLAY_URL" -O "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" 2>/dev/null; then
            # Verify it's not an HTML error page
            if [ -s "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" ] && ! head -c 100 "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" | grep -qi "<!DOCTYPE\|<html"; then
                echo -e "  ${GREEN}[+]${NC} Downloaded pre-built overlay (branch $KERNEL_BRANCH)"
                INSTALL_SUCCESS=true
            else
                rm -f "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo"
            fi
        fi
    fi

    # Method 4: Last resort - try main branch from HinTak's fork
    if [ "$INSTALL_SUCCESS" = false ]; then
        echo "  Trying HinTak's main branch as last resort..."
        OVERLAY_URL="https://github.com/HinTak/seeed-voicecard/raw/main/seeed-2mic-voicecard.dtbo"
        if wget -q --timeout=30 "$OVERLAY_URL" -O "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" 2>/dev/null; then
            if [ -s "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" ] && ! head -c 100 "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" | grep -qi "<!DOCTYPE\|<html"; then
                echo -e "  ${YELLOW}[+]${NC} Downloaded overlay from main branch (may not match kernel $KERNEL_BRANCH)"
                INSTALL_SUCCESS=true
            else
                rm -f "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo"
            fi
        fi
    fi

    # Configure config.txt if overlay was installed
    if [ "$INSTALL_SUCCESS" = true ] && [ -f "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" ]; then
        # Add dtoverlay to config.txt if not already present
        if ! grep -q "dtoverlay=seeed-2mic-voicecard" "$CONFIG_FILE" 2>/dev/null; then
            echo "" >> "$CONFIG_FILE"
            echo "# Seeed 2-Mic Voice Card" >> "$CONFIG_FILE"
            echo "dtoverlay=seeed-2mic-voicecard" >> "$CONFIG_FILE"
            echo -e "  ${GREEN}[+]${NC} Added overlay to config.txt"
        fi

        # Ensure I2C and I2S are enabled
        if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE" 2>/dev/null; then
            echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
        fi
        if ! grep -q "^dtparam=i2s=on" "$CONFIG_FILE" 2>/dev/null; then
            echo "dtparam=i2s=on" >> "$CONFIG_FILE"
        fi

        echo -e "${GREEN}[OK]${NC} Seeed Voice Card driver installed"
        NEED_REBOOT=true
    else
        echo -e "${RED}[!] Failed to install Seeed Voice Card driver${NC}"
        echo -e "${YELLOW}    You may need to install it manually.${NC}"
        echo -e "${YELLOW}    For kernel $KERNEL_BRANCH, try:${NC}"
        echo -e "${YELLOW}      git clone -b $KERNEL_BRANCH https://github.com/HinTak/seeed-voicecard${NC}"
        echo -e "${YELLOW}    See: https://github.com/respeaker/seeed-voicecard/issues/346${NC}"
    fi

    cd "$PROJECT_DIR"
    rm -rf "$TEMP_DIR"

    if [ "$NEED_REBOOT" = true ]; then
        echo -e "${YELLOW}[!] REBOOT REQUIRED after installation completes${NC}"
    fi
fi
echo ""

# ============================================
# Step 4: Create Python Virtual Environment
# ============================================
echo -e "${BLUE}[4/7] Setting up Python virtual environment...${NC}"

VENV_DIR="$PROJECT_DIR/venv"

# Check if venv exists and is valid
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ] && [ -f "$VENV_DIR/bin/pip" ]; then
    # Verify venv is functional
    if "$VENV_DIR/bin/python" -c "import sys; sys.exit(0)" 2>/dev/null; then
        echo -e "${GREEN}[OK]${NC} Python virtual environment already exists"
        VENV_CREATED=false
    else
        echo -e "  [~] Existing venv is broken, recreating..."
        rm -rf "$VENV_DIR"
        VENV_CREATED=true
    fi
else
    if [ -d "$VENV_DIR" ]; then
        echo -e "  [~] Removing incomplete venv..."
        rm -rf "$VENV_DIR"
    fi
    VENV_CREATED=true
fi

if [ "$VENV_CREATED" = true ]; then
    python3 -m venv "$VENV_DIR"
    echo -e "  [+] Virtual environment created"

    # Activate venv
    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --upgrade pip -qq
    echo -e "  [+] pip upgraded"
    echo -e "${GREEN}[OK]${NC} Python environment ready"
else
    # Still need to activate for subsequent steps
    source "$VENV_DIR/bin/activate"
fi
echo ""

# ============================================
# Step 5: Install Python Dependencies
# ============================================
echo -e "${BLUE}[5/7] Installing Python dependencies...${NC}"

# Check if key packages are already installed to skip slow pip install
# These are the slowest/most important packages from requirements.txt
KEY_PACKAGES=("vosk" "pyaudio" "piper" "google.genai")
PACKAGES_MISSING=false

for pkg in "${KEY_PACKAGES[@]}"; do
    if ! "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
        PACKAGES_MISSING=true
        break
    fi
done

if [ "$PACKAGES_MISSING" = true ]; then
    echo -e "  [~] Installing Python packages (this may take a while on first run)..."
    pip install -r "$PROJECT_DIR/requirements.txt" -qq
    echo -e "${GREEN}[OK]${NC} Python dependencies installed"
else
    echo -e "${GREEN}[OK]${NC} Python dependencies already installed"
fi
echo ""

# ============================================
# Step 6: Download Local AI Models
# ============================================
if [ "$SKIP_MODELS" = true ]; then
    echo -e "${BLUE}[6/7] Skipping local AI models (--skip-models)${NC}"
else
    echo -e "${BLUE}[6/7] Downloading local AI models...${NC}"

    # Create model directories
    mkdir -p "$PROJECT_DIR/models/stt"
    mkdir -p "$PROJECT_DIR/models/tts"

    # Helper function for downloading with progress
    download_with_progress() {
        local url="$1"
        local output="$2"
        local description="$3"

        echo -ne "  [~] Downloading $description... "

        # Try wget first (follows redirects with -L flag for GitHub/HuggingFace)
        if command -v wget &>/dev/null; then
            if wget --timeout=120 --tries=3 -q -L "$url" -O "$output" 2>/dev/null; then
                # Verify file was actually downloaded (not empty or HTML error)
                if [ -s "$output" ] && ! head -c 100 "$output" | grep -qi "<!DOCTYPE\|<html"; then
                    echo -e "${GREEN}done${NC}"
                    return 0
                fi
            fi
        fi

        # Fallback to curl
        if command -v curl &>/dev/null; then
            rm -f "$output" 2>/dev/null
            if curl -fSL --connect-timeout 30 --max-time 300 -o "$output" "$url" 2>/dev/null; then
                if [ -s "$output" ] && ! head -c 100 "$output" | grep -qi "<!DOCTYPE\|<html"; then
                    echo -e "${GREEN}done${NC}"
                    return 0
                fi
            fi
        fi

        rm -f "$output" 2>/dev/null
        echo -e "${RED}failed${NC}"
        return 1
    }

    # Download Vosk STT model
    STT_MODEL_DIR="$PROJECT_DIR/models/stt/$VOSK_MODEL_NAME"
    if [ -d "$STT_MODEL_DIR" ]; then
        echo -e "  ${GREEN}[+]${NC} Vosk STT model already downloaded"
    else
        cd "$PROJECT_DIR/models/stt"
        if download_with_progress "$VOSK_MODEL_URL" "model.zip" "Vosk STT model (~40MB)"; then
            echo -ne "  [~] Extracting... "
            if unzip -q model.zip 2>/dev/null; then
                rm -f model.zip
                echo -e "${GREEN}done${NC}"
                echo -e "  ${GREEN}[+]${NC} Vosk STT model installed"
            else
                echo -e "${RED}failed${NC}"
                rm -f model.zip
            fi
        fi
    fi

    # Download Piper binary (standalone, includes phonemizer for ARM64)
    PIPER_INSTALL_DIR="/usr/local/bin"
    PIPER_LIB_DIR="/usr/local/lib/piper"
    if [ -f "$PIPER_INSTALL_DIR/piper" ] && [ -d "$PIPER_LIB_DIR" ]; then
        echo -e "  ${GREEN}[+]${NC} Piper binary already installed"
    else
        cd "$PROJECT_DIR/models"
        if download_with_progress "$PIPER_BINARY_URL" "piper.tar.gz" "Piper binary (~30MB)"; then
            echo -ne "  [~] Installing piper binary... "
            if tar -xzf piper.tar.gz 2>/dev/null; then
                # Install binary to /usr/local/bin
                cp piper/piper "$PIPER_INSTALL_DIR/"
                chmod +x "$PIPER_INSTALL_DIR/piper"

                # Install shared libraries to dedicated directory
                mkdir -p "$PIPER_LIB_DIR"
                cp piper/lib*.so* "$PIPER_LIB_DIR/" 2>/dev/null || cp piper/*.so* "$PIPER_LIB_DIR/" 2>/dev/null || true

                # Copy espeak-ng data that piper needs
                mkdir -p /usr/local/share/piper
                cp -r piper/espeak-ng-data /usr/local/share/piper/ 2>/dev/null || true

                # Add piper lib directory to ldconfig
                echo "$PIPER_LIB_DIR" > /etc/ld.so.conf.d/piper.conf
                ldconfig

                rm -rf piper piper.tar.gz
                echo -e "${GREEN}done${NC}"
                echo -e "  ${GREEN}[+]${NC} Piper binary installed to $PIPER_INSTALL_DIR/piper"
            else
                echo -e "${RED}failed${NC}"
                rm -f piper.tar.gz
            fi
        fi
    fi

    # Download Piper TTS voices (model + config from Hugging Face)
    TTS_MODEL_DIR="$PROJECT_DIR/models/tts"
    cd "$TTS_MODEL_DIR"

    # Download English voice
    if [ -f "$TTS_MODEL_DIR/$PIPER_VOICE_EN_NAME.onnx" ] && [ -f "$TTS_MODEL_DIR/$PIPER_VOICE_EN_NAME.onnx.json" ]; then
        echo -e "  ${GREEN}[+]${NC} Piper English voice already downloaded"
    else
        PIPER_EN_OK=true
        if [ ! -f "$PIPER_VOICE_EN_NAME.onnx" ]; then
            if ! download_with_progress "$PIPER_VOICE_EN_URL" "$PIPER_VOICE_EN_NAME.onnx" "Piper English voice (~65MB)"; then
                PIPER_EN_OK=false
            fi
        fi
        if [ ! -f "$PIPER_VOICE_EN_NAME.onnx.json" ]; then
            if ! download_with_progress "$PIPER_VOICE_EN_JSON_URL" "$PIPER_VOICE_EN_NAME.onnx.json" "Piper English config"; then
                PIPER_EN_OK=false
            fi
        fi
        if [ "$PIPER_EN_OK" = true ]; then
            echo -e "  ${GREEN}[+]${NC} Piper English voice installed"
        else
            echo -e "  ${RED}[!]${NC} Piper English voice download failed"
        fi
    fi

    # Download German voice (Thorsten High)
    if [ -f "$TTS_MODEL_DIR/$PIPER_VOICE_DE_NAME.onnx" ] && [ -f "$TTS_MODEL_DIR/$PIPER_VOICE_DE_NAME.onnx.json" ]; then
        echo -e "  ${GREEN}[+]${NC} Piper German voice already downloaded"
    else
        PIPER_DE_OK=true
        if [ ! -f "$PIPER_VOICE_DE_NAME.onnx" ]; then
            if ! download_with_progress "$PIPER_VOICE_DE_URL" "$PIPER_VOICE_DE_NAME.onnx" "Piper German voice (~100MB)"; then
                PIPER_DE_OK=false
            fi
        fi
        if [ ! -f "$PIPER_VOICE_DE_NAME.onnx.json" ]; then
            if ! download_with_progress "$PIPER_VOICE_DE_JSON_URL" "$PIPER_VOICE_DE_NAME.onnx.json" "Piper German config"; then
                PIPER_DE_OK=false
            fi
        fi
        if [ "$PIPER_DE_OK" = true ]; then
            echo -e "  ${GREEN}[+]${NC} Piper German voice installed"
        else
            echo -e "  ${RED}[!]${NC} Piper German voice download failed"
        fi
    fi

    cd "$PROJECT_DIR"

    # Verify models and binary were downloaded
    MODELS_OK=true
    [ ! -d "$PROJECT_DIR/models/stt/$VOSK_MODEL_NAME" ] && MODELS_OK=false
    [ ! -f "$PROJECT_DIR/models/tts/$PIPER_VOICE_EN_NAME.onnx" ] && MODELS_OK=false
    [ ! -f "$PROJECT_DIR/models/tts/$PIPER_VOICE_DE_NAME.onnx" ] && MODELS_OK=false
    [ ! -f "/usr/local/bin/piper" ] && MODELS_OK=false
    [ ! -d "/usr/local/lib/piper" ] && MODELS_OK=false

    if [ "$MODELS_OK" = true ]; then
        echo -e "${GREEN}[OK]${NC} Local AI models and Piper binary ready"
    elif [ -d "$PROJECT_DIR/models/stt/$VOSK_MODEL_NAME" ] || [ -f "$PROJECT_DIR/models/tts/$PIPER_VOICE_EN_NAME.onnx" ] || [ -f "/usr/local/bin/piper" ]; then
        echo -e "${YELLOW}[!]${NC} Some components failed to download (check errors above)"
    else
        echo -e "${RED}[!]${NC} Failed to download models and Piper binary"
    fi
fi
echo ""

# ============================================
# Step 7: Setup Configuration
# ============================================
echo -e "${BLUE}[7/7] Setting up configuration...${NC}"

# Handle .env file - preserve existing if API key not explicitly provided
if [ "$PROVIDER" != "skip" ]; then
    if [ -f "$PROJECT_DIR/.env" ] && [ -z "$API_KEY" ]; then
        # .env exists and no new API key provided - preserve it, just update provider
        echo -e "  [+] Preserving existing .env file"
        update_settings_provider "$PROVIDER"
        echo -e "  [+] Set active provider: $PROVIDER"
    else
        # Create/overwrite .env with new API key
        write_env_file "$PROVIDER" "$API_KEY"
        echo -e "  [+] Created .env file"
        update_settings_provider "$PROVIDER"
        echo -e "  [+] Set active provider: $PROVIDER"
    fi
elif [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo -e "  [+] Created .env file (add your API key later)"
else
    echo -e "  [+] Configuration files already exist"
fi

# Fix permissions
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_DIR"
chmod +x "$PROJECT_DIR/scripts/"*.sh
chmod 600 "$PROJECT_DIR/.env" 2>/dev/null || true

echo -e "${GREEN}[OK]${NC} Configuration complete"
echo ""

# ============================================
# Installation Summary
# ============================================
echo ""
echo -e "${BLUE}=================================================="
echo "  INSTALLATION SUMMARY"
echo "==================================================${NC}"
echo ""

# Track overall status
TOTAL_OK=0
TOTAL_WARN=0
TOTAL_FAIL=0
ERRORS=()

# Helper function to print status line
print_status() {
    local component="$1"
    local status="$2"
    local detail="$3"

    # Pad component name to 30 chars for alignment
    printf "  %-32s" "$component"

    case "$status" in
        ok)
            echo -e "${GREEN}[OK]${NC}     $detail"
            ((TOTAL_OK++))
            ;;
        warn)
            echo -e "${YELLOW}[WARN]${NC}   $detail"
            ((TOTAL_WARN++))
            ;;
        fail)
            echo -e "${RED}[FAIL]${NC}   $detail"
            ((TOTAL_FAIL++))
            ERRORS+=("$component: $detail")
            ;;
    esac
}

echo -e "${CYAN}System Components:${NC}"
echo "  --------------------------------  ------   --------"

# Check system packages
MISSING_SYS_PKGS=()
for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        MISSING_SYS_PKGS+=("$pkg")
    fi
done
if [ ${#MISSING_SYS_PKGS[@]} -eq 0 ]; then
    print_status "System packages" "ok" "All ${#PACKAGES[@]} packages installed"
else
    print_status "System packages" "fail" "Missing: ${MISSING_SYS_PKGS[*]}"
fi

# Check Seeed Voice Card driver
if arecord -l 2>/dev/null | grep -qi "seeed"; then
    print_status "Seeed Voice Card driver" "ok" "Active and working"
elif [ -f "/boot/overlays/seeed-2mic-voicecard.dtbo" ] || [ -f "/boot/firmware/overlays/seeed-2mic-voicecard.dtbo" ]; then
    print_status "Seeed Voice Card driver" "warn" "Installed (reboot required)"
else
    print_status "Seeed Voice Card driver" "fail" "Not found"
fi

# Check I2C
if [ -e /dev/i2c-1 ]; then
    print_status "I2C interface" "ok" "Enabled"
else
    print_status "I2C interface" "warn" "Not enabled (run raspi-config)"
fi

# Check SPI (often needed for HATs)
if [ -e /dev/spidev0.0 ]; then
    print_status "SPI interface" "ok" "Enabled"
else
    print_status "SPI interface" "warn" "Not enabled (may not be needed)"
fi

echo ""
echo -e "${CYAN}Python Environment:${NC}"
echo "  --------------------------------  ------   --------"

# Check Python venv
if [ -d "$PROJECT_DIR/venv" ] && [ -f "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON_VER=$("$PROJECT_DIR/venv/bin/python" --version 2>&1 | cut -d' ' -f2)
    print_status "Virtual environment" "ok" "Python $PYTHON_VER"
else
    print_status "Virtual environment" "fail" "Missing or broken"
fi

# Check critical Python packages with versions
declare -A PY_PACKAGES=(
    ["vosk"]="Speech recognition"
    ["pyaudio"]="Audio I/O"
    ["piper-tts"]="Text-to-speech"
    ["google-genai"]="Gemini API"
    ["openai"]="OpenAI API"
    ["python-dotenv"]="Config loading"
)

for pkg in "${!PY_PACKAGES[@]}"; do
    if [ -f "$PROJECT_DIR/venv/bin/pip" ]; then
        pkg_info=$("$PROJECT_DIR/venv/bin/pip" show "$pkg" 2>/dev/null)
        if [ -n "$pkg_info" ]; then
            pkg_ver=$(echo "$pkg_info" | grep "^Version:" | cut -d' ' -f2)
            print_status "$pkg" "ok" "v$pkg_ver"
        else
            print_status "$pkg" "fail" "Not installed"
        fi
    else
        print_status "$pkg" "fail" "venv missing"
    fi
done

echo ""
echo -e "${CYAN}AI Models:${NC}"
echo "  --------------------------------  ------   --------"

if [ "$SKIP_MODELS" = true ]; then
    print_status "Vosk STT model" "warn" "Skipped (--skip-models)"
    print_status "Piper binary" "warn" "Skipped (--skip-models)"
    print_status "Piper English voice" "warn" "Skipped (--skip-models)"
    print_status "Piper German voice" "warn" "Skipped (--skip-models)"
else
    # Check STT model
    if [ -d "$PROJECT_DIR/models/stt/$VOSK_MODEL_NAME" ]; then
        model_size=$(du -sh "$PROJECT_DIR/models/stt/$VOSK_MODEL_NAME" 2>/dev/null | cut -f1)
        print_status "Vosk STT model" "ok" "$VOSK_MODEL_NAME ($model_size)"
    else
        print_status "Vosk STT model" "fail" "Not downloaded"
    fi

    # Check Piper binary
    if [ -f "/usr/local/bin/piper" ]; then
        piper_ver=$(/usr/local/bin/piper --version 2>&1 | head -1 || echo "installed")
        print_status "Piper binary" "ok" "$piper_ver"
    else
        print_status "Piper binary" "fail" "Not installed"
    fi

    # Check English TTS voice
    if [ -f "$PROJECT_DIR/models/tts/$PIPER_VOICE_EN_NAME.onnx" ]; then
        model_size=$(du -sh "$PROJECT_DIR/models/tts/$PIPER_VOICE_EN_NAME.onnx" 2>/dev/null | cut -f1)
        print_status "Piper English voice" "ok" "$PIPER_VOICE_EN_NAME ($model_size)"
    else
        print_status "Piper English voice" "fail" "Not downloaded"
    fi

    # Check German TTS voice
    if [ -f "$PROJECT_DIR/models/tts/$PIPER_VOICE_DE_NAME.onnx" ]; then
        model_size=$(du -sh "$PROJECT_DIR/models/tts/$PIPER_VOICE_DE_NAME.onnx" 2>/dev/null | cut -f1)
        print_status "Piper German voice" "ok" "$PIPER_VOICE_DE_NAME ($model_size)"
    else
        print_status "Piper German voice" "fail" "Not downloaded"
    fi
fi

echo ""
echo -e "${CYAN}Configuration:${NC}"
echo "  --------------------------------  ------   --------"

# Check .env file
if [ -f "$PROJECT_DIR/.env" ]; then
    # Check if API key is configured
    if [ "$PROVIDER" != "skip" ]; then
        env_var=$(get_env_var_name "$PROVIDER")
        if grep -q "^${env_var}=.\+" "$PROJECT_DIR/.env" 2>/dev/null; then
            print_status "Environment (.env)" "ok" "$PROVIDER API key configured"
        else
            print_status "Environment (.env)" "warn" "API key not set"
        fi
    else
        print_status "Environment (.env)" "ok" "File exists"
    fi
else
    print_status "Environment (.env)" "fail" "File missing"
fi

# Check settings.json
if [ -f "$PROJECT_DIR/config/settings.json" ]; then
    print_status "Settings (settings.json)" "ok" "Provider: $PROVIDER"
else
    print_status "Settings (settings.json)" "warn" "Missing (will use defaults)"
fi

# Check personas directory
if [ -d "$PROJECT_DIR/personas" ] && [ "$(ls -A "$PROJECT_DIR/personas" 2>/dev/null)" ]; then
    persona_count=$(find "$PROJECT_DIR/personas" -name "*.json" 2>/dev/null | wc -l)
    print_status "Personas" "ok" "$persona_count persona(s) found"
else
    print_status "Personas" "warn" "No personas configured"
fi

# ============================================
# Final Status
# ============================================
echo ""
echo "  ================================  ======   ========"
echo ""

# Calculate totals
TOTAL=$((TOTAL_OK + TOTAL_WARN + TOTAL_FAIL))

if [ $TOTAL_FAIL -eq 0 ] && [ $TOTAL_WARN -eq 0 ]; then
    echo -e "  ${GREEN}✓ ALL COMPONENTS INSTALLED SUCCESSFULLY${NC}"
    echo -e "    $TOTAL_OK/$TOTAL components OK"
elif [ $TOTAL_FAIL -eq 0 ]; then
    echo -e "  ${GREEN}✓ INSTALLATION COMPLETE${NC} ${YELLOW}(with $TOTAL_WARN warning(s))${NC}"
    echo -e "    ${GREEN}$TOTAL_OK OK${NC}, ${YELLOW}$TOTAL_WARN warning(s)${NC}"
else
    echo -e "  ${RED}✗ INSTALLATION INCOMPLETE${NC}"
    echo -e "    ${GREEN}$TOTAL_OK OK${NC}, ${YELLOW}$TOTAL_WARN warning(s)${NC}, ${RED}$TOTAL_FAIL failed${NC}"
    echo ""
    echo -e "  ${RED}Errors:${NC}"
    for err in "${ERRORS[@]}"; do
        echo -e "    ${RED}•${NC} $err"
    done
fi

echo ""

# Show reboot notice if needed
if [ "$NEED_REBOOT" = true ]; then
    echo -e "  ${YELLOW}⚠ REBOOT REQUIRED${NC}"
    echo -e "    Audio driver needs a reboot to activate."
    echo ""
fi

# Show API key notice if not configured
if [ "$PROVIDER" != "skip" ] && [ -z "$API_KEY" ]; then
    if ! grep -q "^$(get_env_var_name "$PROVIDER")=.\+" "$PROJECT_DIR/.env" 2>/dev/null; then
        echo -e "  ${YELLOW}⚠ API KEY REQUIRED${NC}"
        echo -e "    Add your $PROVIDER API key to: $PROJECT_DIR/.env"
        echo ""
    fi
fi

# ============================================
# Next Steps
# ============================================
echo -e "${BLUE}--------------------------------------------------${NC}"
echo -e "${CYAN}NEXT STEPS:${NC}"
echo ""

STEP=1

if [ "$NEED_REBOOT" = true ]; then
    echo -e "  ${STEP}. ${YELLOW}Reboot your Raspberry Pi${NC}"
    echo "     sudo reboot"
    echo ""
    ((STEP++))
fi

if [ "$PROVIDER" != "skip" ]; then
    env_var=$(get_env_var_name "$PROVIDER")
    if ! grep -q "^${env_var}=.\+" "$PROJECT_DIR/.env" 2>/dev/null; then
        echo -e "  ${STEP}. Add your ${PROVIDER^} API key"
        echo "     nano $PROJECT_DIR/.env"
        echo ""
        ((STEP++))
    fi
fi

echo -e "  ${STEP}. Activate the virtual environment"
echo "     source $PROJECT_DIR/venv/bin/activate"
echo ""
((STEP++))

echo -e "  ${STEP}. Run DODI"
echo "     python $PROJECT_DIR/src/main.py"
echo ""

echo -e "${BLUE}==================================================${NC}"

if [ "$NEED_REBOOT" = true ]; then
    echo ""
    read -p "Reboot now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        reboot
    else
        echo ""
        echo "Run 'sudo reboot' when ready."
    fi
fi
