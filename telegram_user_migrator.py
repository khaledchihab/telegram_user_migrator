import asyncio
from pyrogram import Client, errors
from pyrogram.types import User
from pyrogram.errors import FloodWait, UserPrivacyRestricted, PeerIdInvalid, UserNotMutualContact
import time
from datetime import datetime
import os
import json
import argparse
import logging
from typing import Optional, Tuple, List

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('migration.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class MigrationError(Exception):
    """Base exception for migration errors"""
    pass

class GroupValidationError(MigrationError):
    """Raised when group validation fails"""
    pass

class TelegramMigrator:
    def __init__(self, api_id: str, api_hash: str, session_name: str = "user_migration"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.client = None
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": {}
        }
        self.start_time = None
        self.dry_run = False

    async def start(self):
        """Initialize and start the Pyrogram client"""
        try:
            self.client = Client(self.session_name, api_id=self.api_id, api_hash=self.api_hash)
            await self.client.start()
            me = await self.client.get_me()
            logger.info(f"\nConnected as: {me.first_name} ({me.id})")
        except Exception as e:
            logger.error(f"Failed to start client: {e}")
            raise MigrationError(f"Failed to start client: {e}")

    async def stop(self):
        """Stop the Pyrogram client"""
        if self.client:
            try:
                await self.client.stop()
            except Exception as e:
                logger.error(f"Error stopping client: {e}")

    async def validate_group(self, chat_id: str) -> Tuple[Optional[any], bool]:
        """Validate group and return chat info"""
        try:
            # If input starts with '@', it's a username
            # If it starts with '-100', it's a group ID
            # If it's numeric, try converting to proper format
            if chat_id.startswith('@'):
                chat = await self.client.get_chat(chat_id)
            elif chat_id.startswith('-100'):
                chat = await self.client.get_chat(int(chat_id))
            elif chat_id.isdigit():
                formatted_id = int(f"-100{chat_id}")
                chat = await self.client.get_chat(formatted_id)
            else:
                try:
                    chat = await self.client.get_chat(int(chat_id))
                except:
                    formatted_id = int(f"-100{chat_id.replace('-', '')}")
                    chat = await self.client.get_chat(formatted_id)
            
            logger.info(f"\nGroup Info:")
            logger.info(f"Title: {chat.title}")
            logger.info(f"ID: {chat.id}")
            if chat.username:
                logger.info(f"Username: @{chat.username}")
            else:
                logger.info("This is a private group")
            
            # Verify bot permissions
            if not chat.permissions:
                raise GroupValidationError("Bot doesn't have permission to view group members")
            
            return chat, True
            
        except GroupValidationError as e:
            logger.error(f"Group validation error: {e}")
            return None, False
        except Exception as e:
            logger.error(f"Error accessing group: {e}")
            return None, False

    async def get_chat_members(self, chat_id: str, filter_bots: bool = True) -> List[User]:
        """Get all members from a chat"""
        try:
            members = []
            logger.info("\nFetching members...")
            async for member in self.client.get_chat_members(chat_id):
                if filter_bots and (member.user.is_bot or member.user.is_deleted):
                    self.stats["skipped"] += 1
                    continue
                members.append(member)
            return members
        except Exception as e:
            logger.error(f"Error getting members: {e}")
            return []

    async def add_user(self, chat_id: str, user: User) -> bool:
        """Add a user to a chat"""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would add user {user.first_name} ({user.id})")
            return True

        try:
            await self.client.add_chat_members(chat_id, user.id)
            logger.info(f"✅ Successfully added user {user.first_name} ({user.id})")
            return True
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"⏳ Rate limit hit. Waiting {wait_time} seconds...")
            await asyncio.sleep(wait_time)
            return False
        except UserPrivacyRestricted:
            logger.warning(f"🔒 Cannot add user {user.first_name} ({user.id}): Privacy settings restricted")
            self._update_error_stats("Privacy Restricted")
            return False
        except UserNotMutualContact:
            logger.warning(f"👥 Cannot add user {user.first_name} ({user.id}): Not a mutual contact")
            self._update_error_stats("Not Mutual Contact")
            return False
        except PeerIdInvalid:
            logger.warning(f"❌ Cannot add user {user.first_name} ({user.id}): Invalid user")
            self._update_error_stats("Invalid User")
            return False
        except Exception as e:
            logger.error(f"❌ Error adding user {user.first_name} ({user.id}): {e}")
            self._update_error_stats(str(e))
            return False

    def _update_error_stats(self, error_type: str):
        """Update error statistics"""
        if error_type not in self.stats["errors"]:
            self.stats["errors"][error_type] = 0
        self.stats["errors"][error_type] += 1

    def save_migration_report(self, source_chat, target_chat):
        """Save migration report to a file"""
        end_time = time.time()
        duration = end_time - self.start_time
        
        # Get current timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        report = {
            "migration_info": {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": round(duration, 2),
                "duration_formatted": f"{int(duration // 60)}m {int(duration % 60)}s",
                "source_group": {
                    "title": source_chat.title,
                    "type": str(source_chat.type),  # Convert type to string
                    "members_count": source_chat.members_count,
                    "username": source_chat.username
                },
                "target_group": {
                    "title": target_chat.title,
                    "type": str(target_chat.type),  # Convert type to string
                    "members_count": target_chat.members_count,
                    "username": target_chat.username
                }
            },
            "statistics": {
                "total_processed": self.stats["total"],
                "successfully_moved": self.stats["success"],
                "failed_to_move": self.stats["failed"],
                "skipped_users": self.stats["skipped"],
                "success_rate_percentage": round((self.stats["success"] / self.stats["total"]) * 100 if self.stats["total"] > 0 else 0, 2),
                "average_time_per_user": round(duration / self.stats["total"] if self.stats["total"] > 0 else 0, 2)
            },
            "errors_breakdown": self.stats["errors"] if self.stats["errors"] else "No errors occurred"
        }
        
        # Create reports directory if it doesn't exist
        if not os.path.exists("migration_reports"):
            os.makedirs("migration_reports")
        
        # Save as both JSON and readable text
        json_filename = f"migration_reports/report_{timestamp}.json"
        text_filename = f"migration_reports/report_{timestamp}.txt"
        
        # Save JSON report
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)
            
        # Save human-readable report
        with open(text_filename, "w", encoding="utf-8") as f:
            f.write("=== Telegram User Migration Report ===\n\n")
            
            f.write("Migration Information:\n")
            f.write(f"Date: {report['migration_info']['date']}\n")
            f.write(f"Duration: {report['migration_info']['duration_formatted']}\n\n")
            
            f.write("Source Group:\n")
            f.write(f"- Title: {report['migration_info']['source_group']['title']}\n")
            f.write(f"- Type: {report['migration_info']['source_group']['type']}\n")
            f.write(f"- Members: {report['migration_info']['source_group']['members_count']}\n")
            f.write(f"- Username: @{report['migration_info']['source_group']['username']}\n\n")
            
            f.write("Target Group:\n")
            f.write(f"- Title: {report['migration_info']['target_group']['title']}\n")
            f.write(f"- Type: {report['migration_info']['target_group']['type']}\n")
            f.write(f"- Members: {report['migration_info']['target_group']['members_count']}\n")
            f.write(f"- Username: @{report['migration_info']['target_group']['username']}\n\n")
            
            f.write("Statistics:\n")
            f.write(f"- Total users processed: {report['statistics']['total_processed']}\n")
            f.write(f"- Successfully moved: {report['statistics']['successfully_moved']}\n")
            f.write(f"- Failed to move: {report['statistics']['failed_to_move']}\n")
            f.write(f"- Skipped users: {report['statistics']['skipped_users']}\n")
            f.write(f"- Success rate: {report['statistics']['success_rate_percentage']}%\n")
            f.write(f"- Average time per user: {report['statistics']['average_time_per_user']} seconds\n\n")
            
            f.write("Errors Breakdown:\n")
            if isinstance(report['errors_breakdown'], dict):
                for error_type, count in report['errors_breakdown'].items():
                    f.write(f"- {error_type}: {count}\n")
            else:
                f.write(f"- {report['errors_breakdown']}\n")
        
        logger.info(f"\nDetailed reports saved to:")
        logger.info(f"- JSON format: {json_filename}")
        logger.info(f"- Text format: {text_filename}")

async def main():
    parser = argparse.ArgumentParser(description='Telegram User Migration Tool')
    parser.add_argument('-a', '--api-id', help='API ID', required=True)
    parser.add_argument('-H', '--api-hash', help='API Hash', required=True)
    parser.add_argument('-s', '--source-group', help='Source group ID/username', required=True)
    parser.add_argument('-t', '--target-group', help='Target group ID/username', required=True)
    parser.add_argument('-d', '--dry-run', action='store_true', help='Perform a dry run')
    args = parser.parse_args()

    logger.info("\n=== Telegram User Migration Tool ===\n")
    
    # Initialize migrator
    migrator = TelegramMigrator(args.api_id, args.api_hash)
    migrator.dry_run = args.dry_run
    
    try:
        # Start the client
        await migrator.start()
        
        # Validate source and target groups
        logger.info("\nValidating source group...")
        source_chat, source_valid = await migrator.validate_group(args.source_group)
        if not source_valid:
            logger.error("❌ Invalid source group. Please check the ID/username and try again.")
            return
        
        logger.info("\nValidating target group...")
        target_chat, target_valid = await migrator.validate_group(args.target_group)
        if not target_valid:
            logger.error("❌ Invalid target group. Please check the ID/username and try again.")
            return
        
        # Both groups are valid, proceed
        logger.info(f"\nSource group validated: {source_chat.title}")
        logger.info(f"Target group validated: {target_chat.title}")
        
        # Start migration process
        migrator.start_time = time.time()
        logger.info("\nFetching members from source group...")
        members = await migrator.get_chat_members(args.source_group)
        
        if not members:
            logger.error("No members found or error occurred! Try again or press Ctrl+C to exit.")
            return
        
        total_members = len(members)
        migrator.stats["total"] = total_members
        logger.info(f"\nFound {total_members} members")
        
        # Move members
        logger.info("\nStarting migration...")
        for i, member in enumerate(members, 1):
            logger.info(f"\nProcessing {i}/{total_members}")
            
            if await migrator.add_user(args.target_group, member.user):
                migrator.stats["success"] += 1
            else:
                migrator.stats["failed"] += 1
            
            # Progress percentage
            progress = (i / total_members) * 100
            logger.info(f"Progress: {progress:.1f}%")
            
            # Small delay to avoid hitting rate limits too frequently
            await asyncio.sleep(2)
        
        # Print and save results
        logger.info(f"\nMigration completed!")
        logger.info(f"Total processed: {migrator.stats['total']}")
        logger.info(f"Successfully moved: {migrator.stats['success']}")
        logger.info(f"Failed to move: {migrator.stats['failed']}")
        logger.info(f"Skipped (bots/deleted): {migrator.stats['skipped']}")
        
        if migrator.stats["errors"]:
            logger.info("\nError breakdown:")
            for error_type, count in migrator.stats["errors"].items():
                logger.info(f"- {error_type}: {count}")
        
        migrator.save_migration_report(source_chat, target_chat)
    
    except KeyboardInterrupt:
        logger.info("\n\nScript interrupted by user.")
    except Exception as e:
        logger.error(f"\nAn error occurred: {e}")
        logger.info("You can try again or press Ctrl+C to exit.")
    finally:
        # Always ensure we properly close the client
        await migrator.stop()

if __name__ == "__main__":
    asyncio.run(main())