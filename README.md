# Telegram User Migration Tool

A Python script for migrating users between Telegram groups efficiently using Pyrogram.

## Features

- 🚀 Fast and efficient user migration
- ✅ Direct user addition without invitation links
- 🤖 Automatic bot and deleted account filtering
- ⏳ Smart rate limit handling
- 📊 Detailed migration statistics and reports
- 🔍 Group validation before migration
- 💾 Migration report generation
- 🎯 Progress tracking with percentage
- ❌ Comprehensive error handling and categorization

## Prerequisites

1. Python 3.9 or higher
2. Visual C++ Build Tools (required for TgCrypto installation)
3. Telegram API Credentials (api_id and api_hash)

## Setup Instructions

### 1. Install Python
- Download Python from [python.org](https://www.python.org/downloads/)
- During installation, make sure to check "Add Python to PATH"
- Verify installation by opening PowerShell or Command Prompt and typing:
  ```
  py --version
  ```

### 2. Install Visual C++ Build Tools
- Download from [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
- Run the installer
- Select "Desktop development with C++"
- Complete the installation

### 3. Install Dependencies
- Open PowerShell or Command Prompt
- Navigate to the script directory:
  ```
  cd path\to\ScriptPyton
  ```
- Install required packages:
  ```
  py -m pip install -r requirements.txt
  ```

### 4. Get Telegram API Credentials
1. Go to [my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Click on "API Development Tools"
4. Create a new application
5. Copy your `api_id` and `api_hash`

## Usage

1. Run the script:
   ```
   py telegram_user_migrator.py
   ```

2. Enter the requested information:
   - API ID (from my.telegram.org)
   - API Hash (from my.telegram.org)
   - Source group ID/username (e.g., "@groupname" or "-100xxxxxxxxxx")
   - Destination group ID/username

3. The script will:
   - Validate both groups
   - Show group names for confirmation
   - Ask for confirmation before proceeding
   - Display real-time progress
   - Generate a detailed report after completion

## Migration Report

After each migration, the script generates a detailed JSON report containing:
- Migration date and duration
- Total number of users processed
- Success and failure counts
- Detailed error breakdown
- Success rate percentage

## Important Notes

- You must be an admin in both source and destination groups
- The script will only migrate active users (no bots or deleted accounts)
- Rate limits are handled automatically with smart waiting
- Progress is shown in real-time with percentage
- Users will be added directly without invitation links
- Failed additions are logged with specific error types

## Error Types and Handling

The script handles various error scenarios:
- 🔒 Privacy Restricted: User's privacy settings prevent addition
- 👥 Not Mutual Contact: User requires mutual contact
- ❌ Invalid User: User account is invalid or deactivated
- ⏳ Rate Limit: Automatic waiting and retry
- Other errors are logged with specific messages

## Troubleshooting

1. If you get "Python not found":
   - Make sure Python is added to PATH
   - Try using `python` or `python3` instead of `py`

2. If TgCrypto fails to install:
   - Ensure Visual C++ Build Tools are properly installed
   - Try restarting your computer after installing Build Tools

3. If you get "Unauthorized":
   - Double-check your API credentials
   - Make sure you're using the correct group IDs/usernames

4. If migration is slow:
   - This is normal due to Telegram's rate limiting
   - The script automatically handles delays
   - Progress percentage helps track completion

For any other issues, check the error message displayed and ensure all prerequisites are met.
