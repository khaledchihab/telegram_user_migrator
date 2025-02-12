# Telegram User Migrator 🚀

A powerful Python script that helps you migrate users between Telegram groups. Perfect for community managers and group administrators who need to move members between groups efficiently.

## Features ✨

- Migrate users between any Telegram groups (public or private)
- Dry-run mode to test before actual migration
- Detailed logging and error reporting
- Progress tracking
- Command-line interface for easy use
- Skip bots and deleted accounts automatically

## Prerequisites 📋

1. **Python 3.9+** installed on your system
   - Download from [Python.org](https://www.python.org/downloads/)
   - Make sure to check "Add Python to PATH" during installation

2. **Visual C++ Build Tools** (Windows only)
   - Required for TgCrypto installation
   - Download from [Microsoft's website](https://visualstudio.microsoft.com/visual-cpp-build-tools/)

3. **Telegram API Credentials**
   - You'll need an `API_ID` and `API_HASH`
   - Get them from [my.telegram.org](https://my.telegram.org/auth)
     1. Log in with your phone number
     2. Click on "API Development Tools"
     3. Fill in the form (any name will do)
     4. Save your `API_ID` and `API_HASH`

## Installation 🔧

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/telegram_user_migrator.git
   cd telegram_user_migrator
   ```

2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

## Usage 🚀

### Basic Command Format
```bash
python telegram_user_migrator.py -a API_ID -H API_HASH -s SOURCE_GROUP -t TARGET_GROUP [-d]
```

### Parameters Explained
- `-a` or `--api-id`: Your Telegram API ID
- `-H` or `--api-hash`: Your Telegram API Hash
- `-s` or `--source-group`: Source group to copy members from
- `-t` or `--target-group`: Target group to add members to
- `-d` or `--dry-run`: Optional. Test the migration without actually moving users

### Group ID Formats
You can specify groups in several ways:
- Username: `@groupname`
- Private group ID: `-1001234567890` or `-100123456789`
- Basic group ID: `-123456789`

### Examples

1. Migrate from public to private group:
   ```bash
   python telegram_user_migrator.py -a 12345 -H "your_api_hash" -s "@sourcegroup" -t "-100987654321"
   ```

2. Test migration with dry-run:
   ```bash
   python telegram_user_migrator.py -a 12345 -H "your_api_hash" -s "@sourcegroup" -t "-100987654321" -d
   ```

## Finding Group IDs 🔍

1. **For Public Groups**: Just use the username with @ (e.g., @groupname)

2. **For Private Groups**:
   - Forward any message from the group to @username_to_id_bot
   - Or use the group's invite link - the numbers after the '/' are part of the ID

## Common Issues & Solutions 🔧

1. **"Peer ID Invalid" Error**
   - Double-check the group ID format
   - Make sure you're a member of both groups
   - Verify that the bot/user has admin rights in the target group

2. **Rate Limiting**
   - The script automatically handles rate limits by waiting
   - Don't worry if you see "Waiting X seconds" messages

3. **Privacy Restrictions**
   - Some users can't be added due to their privacy settings
   - This is normal and the script will skip these users

## Migration Reports 📊

After each run, the script generates detailed reports in:
- `migration_reports/report_YYYYMMDD_HHMMSS.json` (machine-readable)
- `migration_reports/report_YYYYMMDD_HHMMSS.txt` (human-readable)

These reports contain:
- Success/failure counts
- Detailed error messages
- List of skipped users
- Duration and timestamps

## Safety Tips 🛡️

1. Always run with `-d` (dry-run) first to test
2. Keep your API credentials private
3. Don't share the session file
4. Respect Telegram's terms of service
5. Don't use for spam or harassment

## Need Help? ❓

If you encounter any issues:
1. Check the error message in the console
2. Look at the generated report file
3. Verify your permissions in both groups
4. Make sure your API credentials are correct

## License 📝

This project is licensed under the MIT License - see the LICENSE file for details.
