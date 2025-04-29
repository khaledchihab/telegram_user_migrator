import asyncio
from pyrogram import Client, errors, enums
from pyrogram.types import User, Chat
from pyrogram.errors import FloodWait, UserPrivacyRestricted, PeerIdInvalid, UserNotMutualContact
import time
from datetime import datetime
import os
import json
import argparse
import logging
import sys
import signal
import pickle
from typing import Optional, Tuple, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    print("Note: Install 'tqdm' package for a better progress bar experience.")

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

# Constants for rate limiting
INVITE_DELAY = 60  # 60 seconds (1 minute) delay after each successful invite
FLOOD_ERROR_DELAY = 3600  # 3600 seconds (1 hour) delay for peer flood error

# Terminal colors for better readability
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'
    CYAN = '\033[96m'
    PURPLE = '\033[95m'
    
    @staticmethod
    def supports_color():
        """Check if terminal supports colors"""
        plat = sys.platform
        supported_platform = plat != 'Pocket PC' and (plat != 'win32' or 'ANSICON' in os.environ)
        is_a_tty = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
        return supported_platform and is_a_tty

class MigrationError(Exception):
    """Base exception for migration errors"""
    pass

class GroupValidationError(MigrationError):
    """Raised when group validation fails"""
    pass

class PermissionError(MigrationError):
    """Raised when required permissions are missing"""
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
        self.use_color = Colors.supports_color()
        self.current_permissions = {}  # Track permissions for different groups
        self.retry_attempts = 3  # Number of times to retry adding a user before giving up
        self.progress_file = f"{session_name}_progress.pkl"
        self.processed_users = set()  # Track IDs of processed users
        self.should_exit = False  # Flag to indicate graceful exit

    async def start(self):
        """Initialize and start the Pyrogram client"""
        try:
            self.client = Client(self.session_name, api_id=self.api_id, api_hash=self.api_hash)
            await self.client.start()
            me = await self.client.get_me()
            self.log_success(f"\nConnected as: {me.first_name} ({me.id})")
            return True
        except Exception as e:
            self.log_error(f"Failed to start client: {e}")
            if "api_id/api_hash" in str(e).lower():
                self.log_info("Please check your API credentials at https://my.telegram.org/apps")
            raise MigrationError(f"Failed to start client: {e}")

    async def stop(self):
        """Stop the Pyrogram client"""
        if self.client:
            try:
                await self.client.stop()
                self.log_info("Session closed successfully")
            except Exception as e:
                self.log_error(f"Error stopping client: {e}")

    def log_success(self, message):
        """Log success message with color if supported"""
        if self.use_color:
            logger.info(f"{Colors.GREEN}{message}{Colors.END}")
        else:
            logger.info(message)
            
    def log_warning(self, message):
        """Log warning message with color if supported"""
        if self.use_color:
            logger.warning(f"{Colors.YELLOW}{message}{Colors.END}")
        else:
            logger.warning(message)
            
    def log_error(self, message):
        """Log error message with color if supported"""
        if self.use_color:
            logger.error(f"{Colors.RED}{message}{Colors.END}")
        else:
            logger.error(message)
            
    def log_info(self, message):
        """Log info message with color if supported"""
        if self.use_color:
            logger.info(f"{Colors.BLUE}{message}{Colors.END}")
        else:
            logger.info(message)

    async def check_permissions(self, chat_id: str) -> Dict[str, bool]:
        """Check what permissions the current user has in the group"""
        try:
            permissions = {
                "can_invite_users": False,
                "can_add_members": False,
                "is_member": False,
                "is_admin": False,
                "can_manage_chat": False,
            }
            
            chat = await self.client.get_chat(chat_id)
            
            # Check if user is a member of the group
            try:
                member = await self.client.get_chat_member(chat_id, "me")
                permissions["is_member"] = True
                
                # Check admin status and specific permissions
                if member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                    permissions["is_admin"] = True
                    permissions["can_invite_users"] = member.privileges.can_invite_users if hasattr(member, 'privileges') else False
                    permissions["can_manage_chat"] = member.privileges.can_manage_chat if hasattr(member, 'privileges') else False
                
                # For public groups/channels, normal members can sometimes add members
                if chat.username:
                    permissions["can_add_members"] = True
                elif permissions["can_invite_users"]:
                    permissions["can_add_members"] = True
                
            except Exception as e:
                self.log_warning(f"Couldn't verify membership status: {e}")
            
            self.current_permissions[str(chat_id)] = permissions
            return permissions
        
        except Exception as e:
            self.log_error(f"Error checking permissions: {e}")
            return {"error": str(e)}

    async def validate_group(self, chat_id: str) -> Tuple[Optional[Chat], bool]:
        """Validate group and return chat info with improved error messages"""
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
            
            self.log_info(f"\nGroup Info:")
            if self.use_color:
                logger.info(f"{Colors.BOLD}Title:{Colors.END} {chat.title}")
                logger.info(f"{Colors.BOLD}ID:{Colors.END} {chat.id}")
                if chat.username:
                    logger.info(f"{Colors.BOLD}Username:{Colors.END} @{chat.username}")
                else:
                    logger.info(f"{Colors.BOLD}Type:{Colors.END} Private group")
            else:
                logger.info(f"Title: {chat.title}")
                logger.info(f"ID: {chat.id}")
                if chat.username:
                    logger.info(f"Username: @{chat.username}")
                else:
                    logger.info("This is a private group")
            
            # Check the user's permissions in this group
            permissions = await self.check_permissions(chat.id)
            chat_type = "public" if chat.username else "private"
            
            if chat_type == "private" and not permissions.get("can_add_members", False):
                self.log_warning(f"⚠️ You don't have permission to add members to this private group.")
                self.log_info(f"To add members, you need either:")
                self.log_info(f"1. Admin rights with 'Add Users' permission, OR")
                self.log_info(f"2. The group needs to be a public group with a username")
            else:
                if permissions.get("is_admin", False):
                    self.log_success(f"✅ You have admin rights in this group")
                elif permissions.get("can_add_members", False):
                    self.log_success(f"✅ You can add members to this group")
                else:
                    self.log_info(f"ℹ️ This is a {chat_type} group")
                
            return chat, True
            
        except errors.UsernameNotOccupied:
            self.log_error(f"Group validation error: Username does not exist")
            return None, False
        except errors.UsernameInvalid:
            self.log_error(f"Group validation error: Invalid username format")
            return None, False
        except errors.ChannelInvalid:
            self.log_error(f"Group validation error: Invalid channel/group ID")
            return None, False
        except GroupValidationError as e:
            self.log_error(f"Group validation error: {e}")
            return None, False
        except Exception as e:
            self.log_error(f"Error accessing group: {e}")
            return None, False

    async def get_chat_members(self, chat_id: str, filter_bots: bool = True, limit: int = 0) -> List[User]:
        """Get all members from a chat with improved feedback"""
        try:
            members = []
            self.log_info("\nFetching members...")
            
            # Create a counter for member collection
            member_count = 0
            progress_shown = False
            
            # Calculate estimated total if possible
            try:
                chat = await self.client.get_chat(chat_id)
                estimated_total = chat.members_count
                if TQDM_AVAILABLE and estimated_total:
                    pbar = tqdm(total=estimated_total, desc="Collecting members", unit="member")
                    progress_shown = True
            except:
                estimated_total = None
            
            async for member in self.client.get_chat_members(chat_id):
                # Skip bots, deleted, and the user itself
                if filter_bots and (member.user.is_bot or member.user.is_deleted):
                    self.stats["skipped"] += 1
                    continue
                    
                if member.user.is_self:
                    self.stats["skipped"] += 1
                    continue
                
                members.append(member)
                member_count += 1
                
                # Update progress bar if available
                if progress_shown:
                    pbar.update(1)
                elif member_count % 50 == 0:  # Show progress every 50 members
                    self.log_info(f"Collected {member_count} members so far...")
                
                # Stop if we've reached the limit (if specified)
                if limit and member_count >= limit:
                    self.log_info(f"Reached specified limit of {limit} members")
                    break
            
            if progress_shown:
                pbar.close()
                
            self.log_success(f"Successfully collected {len(members)} members")
            return members
        except Exception as e:
            self.log_error(f"Error getting members: {e}")
            return []

    async def add_user(self, chat_id: str, user: User) -> bool:
        """Add a user to a chat with enhanced error handling"""
        # Skip if we've processed this user already
        if user.id in self.processed_users:
            self.log_info(f"Skipping already processed user {user.first_name} ({user.id})")
            return True

        if self.dry_run:
            self.log_info(f"[DRY RUN] Would add user {user.first_name} ({user.id})")
            self.processed_users.add(user.id)
            return True

        try:
            await self.client.add_chat_members(chat_id, user.id)
            full_name = f"{user.first_name} {user.last_name if user.last_name else ''}".strip()
            self.log_success(f"✅ Successfully added user {full_name} ({user.id})")
            
            # Mark as processed
            self.processed_users.add(user.id)
            self.save_progress()  # Save progress after each successful addition
            
            # Wait for the recommended time after each successful addition
            self.log_info(f"Waiting {INVITE_DELAY} seconds before next invite (Telegram recommendation)...")
            await asyncio.sleep(INVITE_DELAY)
            return True
            
        except FloodWait as e:
            wait_time = e.value
            self.log_warning(f"⏳ Rate limit hit. Waiting {wait_time} seconds...")
            self.save_progress()  # Save progress before waiting
            
            try:
                await asyncio.sleep(wait_time)
                return False
            except asyncio.CancelledError:
                self.log_warning("Wait interrupted, progress saved")
                raise
            
        except UserPrivacyRestricted:
            self.log_warning(f"🔒 Cannot add {user.first_name} ({user.id}): Privacy settings restricted")
            self._update_error_stats("Privacy Restricted")
            self.processed_users.add(user.id)  # Still mark as processed to avoid retrying
            return False
        except UserNotMutualContact:
            self.log_warning(f"👥 Cannot add {user.first_name} ({user.id}): Not a mutual contact")
            self._update_error_stats("Not Mutual Contact")
            self.processed_users.add(user.id)
            return False
        except PeerIdInvalid:
            self.log_warning(f"❌ Cannot add {user.first_name} ({user.id}): Invalid user")
            self._update_error_stats("Invalid User")
            self.processed_users.add(user.id)
            return False
        except errors.ChatAdminRequired:
            self.log_warning(f"⚠️ Cannot add users: Admin privileges required")
            self._update_error_stats("Admin Privileges Required")
            self.processed_users.add(user.id)
            return False
        except errors.UserChannelsTooMuch:
            self.log_warning(f"🔄 User {user.first_name} is in too many channels already")
            self._update_error_stats("User In Too Many Channels")
            self.processed_users.add(user.id)
            return False
        except errors.InputUserDeactivated:
            self.log_warning(f"🚷 Cannot add {user.first_name}: User account deleted/deactivated")
            self._update_error_stats("User Deactivated")
            self.processed_users.add(user.id)
            return False
        except errors.ChannelPrivate:
            self.log_error(f"🔒 Cannot access target group: It's private and you're not a member")
            self._update_error_stats("Channel Private")
            self.processed_users.add(user.id)
            return False
        except Exception as e:
            if "PEER_FLOOD" in str(e) or "FLOOD_WAIT" in str(e) or "flood" in str(e).lower():
                self.log_warning(f"🚫 Peer flood error. Waiting {FLOOD_ERROR_DELAY // 60} minutes...")
                self._update_error_stats("Peer Flood Error")
                self.save_progress()  # Save progress before long wait
                
                try:
                    await asyncio.sleep(FLOOD_ERROR_DELAY)
                    return False
                except asyncio.CancelledError:
                    self.log_warning("Flood wait interrupted, progress saved")
                    raise
            else:
                self.log_error(f"❌ Error adding {user.first_name} ({user.id}): {e}")
                self._update_error_stats(str(e))
                self.processed_users.add(user.id)  # Mark as processed to avoid infinite retries
                return False

    async def batch_add_users(self, chat_id: str, users: List[User], batch_size: int = 5, delay: int = 30) -> None:
        """Add users in batches to minimize flood wait errors"""
        if not users:
            return
            
        self.log_info(f"Processing users in batches of {batch_size}")
        
        # Filter out already processed users if resuming
        if self.processed_users:
            users = [u for u in users if u.user.id not in self.processed_users]
            self.log_info(f"Skipping {len(self.processed_users)} already processed users")
            if not users:
                self.log_success("All users have been processed already!")
                return
        
        user_chunks = [users[i:i+batch_size] for i in range(0, len(users), batch_size)]
        
        for i, chunk in enumerate(user_chunks, 1):
            # Check if we should exit gracefully
            if self.should_exit:
                self.log_warning("Exiting after current batch due to interrupt")
                return
                
            self.log_info(f"Processing batch {i}/{len(user_chunks)} ({len(chunk)} users)")
            
            # Process each user in the batch
            batch_success = 0
            for user in chunk:
                try:
                    if await self.add_user(chat_id, user.user):
                        self.stats["success"] += 1
                        batch_success += 1
                    else:
                        self.stats["failed"] += 1
                except asyncio.CancelledError:
                    self.save_progress()
                    self.log_warning("Operation interrupted, progress saved")
                    raise
                    
                # Check for exit signal
                if self.should_exit:
                    self.log_warning("Exiting due to interrupt")
                    return
            
            # Log batch results
            self.log_info(f"Batch {i} complete: {batch_success}/{len(chunk)} successful")
            self.save_progress()  # Save progress after each batch
            
            # Only wait between batches if it's not the last batch
            if i < len(user_chunks) and not self.should_exit:
                self.log_info(f"Waiting {delay} seconds before next batch...")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    self.save_progress()
                    raise

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
        
        # Format duration nicely
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_formatted = ""
        if hours > 0:
            duration_formatted += f"{int(hours)}h "
        if minutes > 0 or hours > 0:
            duration_formatted += f"{int(minutes)}m "
        duration_formatted += f"{int(seconds)}s"
        
        report = {
            "migration_info": {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": round(duration, 2),
                "duration_formatted": duration_formatted,
                "source_group": {
                    "title": source_chat.title,
                    "type": str(source_chat.type),
                    "members_count": source_chat.members_count,
                    "username": source_chat.username
                },
                "target_group": {
                    "title": target_chat.title,
                    "type": str(target_chat.type),
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
            f.write(f"- Username: {('@' + report['migration_info']['source_group']['username']) if report['migration_info']['source_group']['username'] else 'Private Group'}\n\n")
            
            f.write("Target Group:\n")
            f.write(f"- Title: {report['migration_info']['target_group']['title']}\n")
            f.write(f"- Type: {report['migration_info']['target_group']['type']}\n")
            f.write(f"- Members: {report['migration_info']['target_group']['members_count']}\n")
            f.write(f"- Username: {('@' + report['migration_info']['target_group']['username']) if report['migration_info']['target_group']['username'] else 'Private Group'}\n\n")
            
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
        
        self.log_success(f"\nDetailed reports saved to:")
        self.log_info(f"- JSON format: {json_filename}")
        self.log_info(f"- Text format: {text_filename}")

    async def generate_invite_link(self, chat_id: str, expire_date: int = None, member_limit: int = None) -> Optional[str]:
        """Generate an invite link for the group (if permissions allow)"""
        try:
            permissions = self.current_permissions.get(str(chat_id), {})
            
            # Check if user can generate invite links
            if permissions.get("is_admin", False) and permissions.get("can_invite_users", False):
                invite_link = await self.client.create_chat_invite_link(
                    chat_id=chat_id,
                    expire_date=expire_date,  # Unix timestamp or None for permanent
                    member_limit=member_limit  # Max number of users or None for unlimited
                )
                return invite_link.invite_link
            else:
                # Try to get the chat's existing invite link
                chat = await self.client.get_chat(chat_id)
                if hasattr(chat, "invite_link") and chat.invite_link:
                    return chat.invite_link
                    
                self.log_warning("You don't have permission to create invite links for this group")
                return None
                
        except Exception as e:
            self.log_error(f"Error generating invite link: {e}")
            return None
            
    async def migrate_by_invite_link(self, target_chat_id: str, users: List[User], 
                                     expire_hours: int = 24, member_limit: int = 100) -> Tuple[str, int]:
        """Generate invite link and notify users via direct message"""
        try:
            # Calculate expiration time (current time + expire_hours)
            expire_date = int(time.time() + (expire_hours * 3600)) if expire_hours else None
            
            # Generate invite link
            invite_link = await self.generate_invite_link(
                target_chat_id, 
                expire_date=expire_date,
                member_limit=member_limit
            )
            
            if not invite_link:
                self.log_error("Failed to generate invite link")
                return None, 0
                
            self.log_success(f"Generated invite link: {invite_link}")
            self.log_info(f"Link will expire in {expire_hours} hours" if expire_hours else "Link will never expire")
            
            if self.dry_run:
                self.log_info("[DRY RUN] Would send invite messages to users")
                return invite_link, 0
                
            # Message users with the invite link
            sent_count = 0
            failed_count = 0
            
            message_template = (
                f"Hello! You're invited to join our new group.\n\n"
                f"Click here to join: {invite_link}\n\n"
                f"This invite link will expire in {expire_hours} hours."
            )
            
            if TQDM_AVAILABLE:
                users_iter = tqdm(users, desc="Sending invites", unit="message")
            else:
                users_iter = users
                self.log_info(f"Sending invite messages to {len(users)} users...")
            
            for i, user in enumerate(users_iter):
                try:
                    # Try to send a message to the user
                    await self.client.send_message(
                        chat_id=user.user.id,
                        text=message_template
                    )
                    sent_count += 1
                    
                    # Add delay to avoid rate limiting
                    if i % 10 == 0:  # Every 10 messages
                        await asyncio.sleep(2)  # Short delay
                        
                except FloodWait as e:
                    self.log_warning(f"Message rate limit hit. Waiting {e.value} seconds...")
                    await asyncio.sleep(e.value)
                    failed_count += 1
                except Exception as e:
                    if "FLOOD_WAIT" in str(e) or "flood" in str(e).lower():
                        self.log_warning(f"🚫 Peer flood error. Waiting {FLOOD_ERROR_DELAY // 60} minutes...")
                        await asyncio.sleep(FLOOD_ERROR_DELAY)
                        failed_count += 1
                    else:
                        self.log_warning(f"Failed to message user {user.user.id}: {e}")
                        failed_count += 1
            
            self.log_success(f"Sent invite link to {sent_count} users ({failed_count} failed)")
            return invite_link, sent_count
            
        except Exception as e:
            self.log_error(f"Error in migrate_by_invite_link: {e}")
            return None, 0
    
    async def retry_failed_users(self, chat_id: str, users: List[User], max_retries: int = 3):
        """Retry adding users that failed on the first attempt"""
        if not users or len(users) == 0:
            return
            
        failed_users = []
        retry_count = 1
        
        self.log_info(f"\nRetrying {len(users)} failed users (attempt {retry_count}/{max_retries})")
        
        while users and retry_count <= max_retries:
            if retry_count > 1:
                # Wait longer between retry attempts
                wait_time = 120 * retry_count  # 2, 4, 6 minutes
                self.log_info(f"Waiting {wait_time} seconds before retry attempt {retry_count}...")
                await asyncio.sleep(wait_time)
                self.log_info(f"\nRetrying {len(users)} failed users (attempt {retry_count}/{max_retries})")
            
            # Track users that fail this retry attempt
            newly_failed = []
            
            for user in users:
                if await self.add_user(chat_id, user.user):
                    self.stats["success"] += 1
                    self.stats["failed"] -= 1  # Decrement failed count as we've now succeeded
                else:
                    newly_failed.append(user)
                    
                # Add a small delay between each user
                await asyncio.sleep(2)
            
            # Update the list of users to retry
            users = newly_failed
            retry_count += 1
        
        if users:  # If we still have users that failed all retries
            self.log_warning(f"Could not add {len(users)} users after {max_retries} retry attempts")
    
    async def analyze_target_group(self, chat_id: str) -> Dict[str, Any]:
        """Analyze target group to provide insights and recommendations"""
        try:
            chat = await self.client.get_chat(chat_id)
            permissions = self.current_permissions.get(str(chat_id), {})
            
            analysis = {
                "group_type": "public" if chat.username else "private",
                "is_admin": permissions.get("is_admin", False),
                "can_add_members": permissions.get("can_add_members", False),
                "invite_link_available": hasattr(chat, "invite_link") and bool(chat.invite_link),
                "recommendations": [],
                "warnings": [],
            }
            
            # Generate recommendations based on group type and permissions
            if analysis["group_type"] == "public":
                analysis["recommendations"].append(
                    "This is a public group. Consider using invite links instead of direct additions."
                )
                
                if not analysis["can_add_members"]:
                    analysis["warnings"].append(
                        "You may not have permission to add members to this group."
                    )
            else:  # Private group
                if not analysis["is_admin"]:
                    analysis["warnings"].append(
                        "You're not an admin in this private group. You need 'Add Users' permission."
                    )
                    
                if not analysis["invite_link_available"] and not analysis["can_add_members"]:
                    analysis["warnings"].append(
                        "You can't add users or create invite links. Migration may fail."
                    )
            
            if analysis["is_admin"] and analysis["group_type"] == "private":
                analysis["recommendations"].append(
                    "As an admin of a private group, direct user addition should work well."
                )
                
            return analysis
            
        except Exception as e:
            self.log_error(f"Error analyzing target group: {e}")
            return {"error": str(e)}

    def save_progress(self):
        """Save current progress to a file for resuming later"""
        try:
            progress_data = {
                "processed_users": self.processed_users,
                "stats": self.stats,
                "timestamp": time.time()
            }
            with open(self.progress_file, 'wb') as f:
                pickle.dump(progress_data, f)
            self.log_info(f"Progress saved to {self.progress_file}")
        except Exception as e:
            self.log_error(f"Failed to save progress: {e}")

    def load_progress(self) -> bool:
        """Load progress from file if it exists"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'rb') as f:
                    progress_data = pickle.load(f)
                
                self.processed_users = progress_data.get("processed_users", set())
                self.stats = progress_data.get("stats", self.stats)
                
                timestamp = progress_data.get("timestamp", 0)
                time_ago = time.time() - timestamp
                hours, remainder = divmod(time_ago, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                timestr = ""
                if hours > 0:
                    timestr += f"{int(hours)}h "
                if minutes > 0 or hours > 0:
                    timestr += f"{int(minutes)}m "
                timestr += f"{int(seconds)}s"
                
                self.log_success(f"Loaded previous progress from {timestr} ago")
                self.log_info(f"Previously processed {len(self.processed_users)} users")
                return True
            except Exception as e:
                self.log_error(f"Failed to load progress file: {e}")
                return False
        return False

    def register_signal_handlers(self):
        """Register signal handlers for graceful exit"""
        if os.name == 'posix':  # Unix/Linux/macOS
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        else:  # Windows doesn't handle signals the same way
            # We'll rely on asyncio's exception handling for KeyboardInterrupt
            pass

    def _signal_handler(self, signum, frame):
        """Signal handler for graceful exit"""
        self.should_exit = True
        self.log_warning(f"Received signal {signum}, will exit after current operation completes")
        self.save_progress()

class MultiAccountMigrator:
    def __init__(self, accounts: List[Dict[str, Any]]):
        """Initialize with multiple account credentials"""
        self.accounts = accounts
        self.migrators = []
        self.current_migrator_index = 0
        self.active_migrators = []
        self.use_color = Colors.supports_color()
        self.dry_run = False
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": {}
        }
        self.start_time = None
        self.account_cooldowns = {}  # Track which accounts are in cooldown
        self.account_performance = {} # Track success rate of each account
        self.permissions_cache = {}   # Cache permissions across accounts
        
        # Create migrators for each account
        for i, account in enumerate(accounts):
            session_name = account.get('session_name', f"user_migration_{i}")
            migrator = TelegramMigrator(account['api_id'], account['api_hash'], session_name)
            self.migrators.append(migrator)
            self.account_cooldowns[i] = 0  # Initially no cooldown
            self.account_performance[i] = {
                "attempts": 0,
                "successes": 0,
                "score": 1.0  # Initial score, will be adjusted based on performance
            }

    async def start_all(self):
        """Start all migrators and keep track of successful ones"""
        self.log_info(f"\nStarting {len(self.migrators)} accounts...")
        self.active_migrators = []
        
        for i, migrator in enumerate(self.migrators):
            try:
                if await migrator.start():
                    self.active_migrators.append(migrator)
                    migrator.dry_run = self.dry_run
                    self.log_success(f"Account {i+1} ({migrator.session_name}) connected successfully")
                    self.account_performance[i]["score"] = 1.0  # Reset score on successful connection
            except Exception as e:
                self.log_warning(f"Failed to start account {i+1}: {e}")
                self.account_performance[i]["score"] = 0  # Mark as unusable
                
        if not self.active_migrators:
            self.log_error("No accounts could be started. Please check credentials.")
            raise MigrationError("No active accounts available")
            
        self.log_success(f"Successfully started {len(self.active_migrators)}/{len(self.migrators)} accounts")
        return len(self.active_migrators) > 0

    async def stop_all(self):
        """Stop all active migrators"""
        for migrator in self.active_migrators:
            await migrator.stop()
        self.log_info(f"All {len(self.active_migrators)} accounts disconnected")

    def log_success(self, message):
        """Log success message with color if supported"""
        prefix = f"[Multi] " if self.use_color else "[Multi-Account] "
        if self.use_color:
            logger.info(f"{Colors.CYAN}{prefix}{Colors.GREEN}{message}{Colors.END}")
        else:
            logger.info(f"{prefix}{message}")
            
    def log_warning(self, message):
        """Log warning message with color if supported"""
        prefix = f"[Multi] " if self.use_color else "[Multi-Account] "
        if self.use_color:
            logger.warning(f"{Colors.CYAN}{prefix}{Colors.YELLOW}{message}{Colors.END}")
        else:
            logger.warning(f"{prefix}{message}")
            
    def log_error(self, message):
        """Log error message with color if supported"""
        prefix = f"[Multi] " if self.use_color else "[Multi-Account] "
        if self.use_color:
            logger.error(f"{Colors.CYAN}{prefix}{Colors.RED}{message}{Colors.END}")
        else:
            logger.error(f"{prefix}{message}")
            
    def log_info(self, message):
        """Log info message with color if supported"""
        prefix = f"[Multi] " if self.use_color else "[Multi-Account] "
        if self.use_color:
            logger.info(f"{Colors.CYAN}{prefix}{Colors.BLUE}{message}{Colors.END}")
        else:
            logger.info(f"{prefix}{message}")

    def get_best_available_migrator(self) -> Optional[Tuple[int, TelegramMigrator]]:
        """Get best performing available migrator that's not in cooldown"""
        now = time.time()
        available_migrators = []
        
        for i, migrator in enumerate(self.active_migrators):
            # Check if account is in cooldown
            cooldown_until = self.account_cooldowns.get(i, 0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                self.log_info(f"Account {i+1} ({migrator.session_name}) in cooldown for {remaining}s more")
                continue
                
            # Add to available migrators with its performance score
            score = self.account_performance.get(i, {}).get("score", 1.0)
            available_migrators.append((i, migrator, score))
        
        if not available_migrators:
            return None
        
        # Sort by performance score (highest first)
        available_migrators.sort(key=lambda x: x[2], reverse=True)
        best_idx, best_migrator, _ = available_migrators[0]
        
        # Rotate among the top 50% performers for load balancing
        top_half = max(1, len(available_migrators) // 2)
        if len(available_migrators) > 1:
            position = self.current_migrator_index % top_half
            if position < len(available_migrators):
                best_idx, best_migrator, _ = available_migrators[position]
            
        self.current_migrator_index = (self.current_migrator_index + 1) % len(self.active_migrators)
        return best_idx, best_migrator
        
    def _update_error_stats(self, error_type: str):
        """Update error statistics"""
        if error_type not in self.stats["errors"]:
            self.stats["errors"][error_type] = 0
        self.stats["errors"][error_type] += 1
        
    def _update_account_performance(self, account_idx: int, success: bool):
        """Update account performance metrics"""
        if account_idx not in self.account_performance:
            self.account_performance[account_idx] = {"attempts": 0, "successes": 0, "score": 1.0}
            
        perf = self.account_performance[account_idx]
        perf["attempts"] += 1
        if success:
            perf["successes"] += 1
            
        # Calculate score based on recent performance
        # Higher weight on recent success/failure with some forgiveness factor
        weight_recent = 0.3  # 30% weight to most recent result
        weight_history = 0.7  # 70% weight to historical performance
        
        current_score = perf["score"]
        success_rate = perf["successes"] / max(1, perf["attempts"])
        
        # Blend recent result with historical performance
        if success:
            new_score = (current_score * weight_history) + weight_recent
        else:
            new_score = (current_score * weight_history) # Unsuccessful attempt reduces score relatively
            
        # Cap the score between 0.1 and 1.0
        perf["score"] = max(0.1, min(1.0, new_score))
        
    def _set_account_cooldown(self, account_idx: int, duration: int):
        """Set an account to cooldown for the specified duration in seconds"""
        self.account_cooldowns[account_idx] = time.time() + duration

    async def check_all_permissions(self, chat_id: str) -> Dict[int, Dict[str, bool]]:
        """Check permissions for all accounts on the specified group"""
        permissions = {}
        
        for i, migrator in enumerate(self.active_migrators):
            try:
                account_perms = await migrator.check_permissions(chat_id)
                permissions[i] = account_perms
                
                # Update cache
                self.permissions_cache[f"{i}_{chat_id}"] = account_perms
                
                # Check if this account can add members
                if account_perms.get("can_add_members", False):
                    self.log_success(f"Account {i+1} ({migrator.session_name}) has permission to add members")
                elif account_perms.get("is_admin", False):
                    self.log_success(f"Account {i+1} ({migrator.session_name}) is an admin")
                else:
                    self.log_warning(f"Account {i+1} ({migrator.session_name}) may not be able to add members")
                
            except Exception as e:
                self.log_warning(f"Failed to check permissions for account {i+1}: {e}")
                permissions[i] = {"error": str(e)}
                
        return permissions

    async def validate_group(self, chat_id: str) -> Tuple[Optional[any], bool]:
        """Validate group using any available migrator"""
        if not self.active_migrators:
            self.log_error("No active accounts to validate groups")
            return None, False
            
        # Try each migrator until one works
        errors = []
        for migrator in self.active_migrators:
            try:
                chat, valid = await migrator.validate_group(chat_id)
                if valid:
                    return chat, True
                errors.append("Invalid group")
            except Exception as e:
                errors.append(str(e))
                
        # If all failed, log the errors
        self.log_error(f"All accounts failed to validate group. Errors: {', '.join(errors)}")
        return None, False

    async def get_chat_members(self, chat_id: str, filter_bots: bool = True, limit: int = 0) -> List[User]:
        """Get all members from a chat using any available migrator"""
        if not self.active_migrators:
            self.log_error("No active accounts to get chat members")
            return []
        
        # Try each migrator until one works
        for migrator in self.active_migrators:
            try:
                members = await migrator.get_chat_members(chat_id, filter_bots, limit)
                if members:
                    self.stats["skipped"] = migrator.stats["skipped"]
                    return members
            except Exception as e:
                self.log_warning(f"Failed to get members with account {migrator.session_name}: {e}")
                
        self.log_error("All accounts failed to get members")
        return []

    async def add_user(self, chat_id: str, user: User) -> bool:
        """Add a user using the best available migrator with smart fallback"""
        if not self.active_migrators:
            self.log_error("No active accounts to add users")
            return False
            
        # Get the best available migrator
        result = self.get_best_available_migrator()
        if not result:
            self.log_warning("All accounts are in cooldown. Waiting for 60 seconds...")
            await asyncio.sleep(60)
            result = self.get_best_available_migrator()
            if not result:
                self.log_error("No accounts available to add users")
                return False
        
        account_idx, migrator = result
        
        # Try to add the user with this migrator
        try:
            success = await migrator.add_user(chat_id, user)
            
            # Update performance metrics
            self._update_account_performance(account_idx, success)
            
            # Special handling for ratelimit & permanent failures
            if not success:
                error_types = list(migrator.stats["errors"].keys())
                last_error = error_types[-1] if error_types else "Unknown"
                
                if last_error == "Peer Flood Error":
                    # Set long cooldown for ONLY this account
                    self.log_warning(f"Account {account_idx+1} hit flood protection, placing in 1-hour cooldown")
                    self._set_account_cooldown(account_idx, FLOOD_ERROR_DELAY)
                    
                    # Try another account as fallback
                    return await self.add_user_with_fallback(chat_id, user, exclude_idx=account_idx)
                
                elif last_error == "Admin Privileges Required":
                    # This is likely a permanent error for this account
                    self.log_warning(f"Account {account_idx+1} lacks admin privileges, marking as lower priority")
                    self.account_performance[account_idx]["score"] *= 0.5  # Reduce score significantly
            
            # Update combined statistics for any new errors
            for error_type, count in migrator.stats["errors"].items():
                if error_type not in self.stats["errors"]:
                    self.stats["errors"][error_type] = 0
                # Only count new errors since last check
                new_errors = count - self.stats["errors"].get(error_type, 0)
                if new_errors > 0:
                    self.stats["errors"][error_type] = self.stats["errors"].get(error_type, 0) + new_errors
                        
            return success
            
        except Exception as e:
            self.log_error(f"Error using account {account_idx+1} to add user: {e}")
            # Penalize this account for unexpected errors
            self._update_account_performance(account_idx, False)
            return await self.add_user_with_fallback(chat_id, user, exclude_idx=account_idx)

    async def add_user_with_fallback(self, chat_id: str, user: User, exclude_idx: int = None) -> bool:
        """Try to add user with any account except the excluded one"""
        attempts = 0
        max_attempts = len(self.active_migrators) - (1 if exclude_idx is not None else 0)
        
        while attempts < max_attempts:
            result = self.get_best_available_migrator()
            if not result:
                break
                
            account_idx, migrator = result
            
            # Skip excluded account
            if account_idx == exclude_idx:
                self.current_migrator_index = (self.current_migrator_index + 1) % len(self.active_migrators)
                continue
                
            try:
                success = await migrator.add_user(chat_id, user)
                self._update_account_performance(account_idx, success)
                
                if success:
                    return True
                    
                # If this account also fails, apply appropriate cooldown
                error_types = list(migrator.stats["errors"].keys())
                last_error = error_types[-1] if error_types else "Unknown"
                
                if last_error == "Peer Flood Error":
                    self._set_account_cooldown(account_idx, FLOOD_ERROR_DELAY)
                    
            except Exception:
                self._update_account_performance(account_idx, False)
                
            attempts += 1
            
        return False

async def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Migrate users from one Telegram group to another",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Core arguments
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-a", "--api-id", help="Telegram API ID")
    group.add_argument("-m", "--multi-account", help="Path to accounts.json file with multiple accounts")
    
    parser.add_argument("-H", "--api-hash", help="Telegram API hash (not required with --multi-account)")
    parser.add_argument("-s", "--source", required=True, help="Source group username or ID")
    parser.add_argument("-t", "--target", required=True, help="Target group username or ID")
    parser.add_argument("-n", "--session-name", default="user_migration", 
                        help="Session name (default: user_migration)")
    
    # Optional features
    parser.add_argument("--dry-run", action="store_true", 
                        help="Perform a dry run without actually adding users")
    parser.add_argument("--batch-size", type=int, default=5, 
                        help="Number of users to process in a batch (default: 5)")
    parser.add_argument("--batch-delay", type=int, default=30, 
                        help="Delay between batches in seconds (default: 30)")
    parser.add_argument("--limit", type=int, default=0, 
                        help="Limit the number of users to migrate (0=unlimited)")
    parser.add_argument("--filter", choices=["active", "all", "recent"], default="active", 
                        help="Filter users: active (exclude deleted/bots), all, recent (default: active)")
    parser.add_argument("--invite-link", action="store_true",
                        help="Use invite link approach instead of direct additions")
    parser.add_argument("--expire-hours", type=int, default=24, 
                        help="Invite link expiration in hours (default: 24)")
    parser.add_argument("--no-retry", action="store_true", 
                        help="Don't retry failed additions")
    parser.add_argument("--no-resume", action="store_true",
                        help="Don't resume from previous progress")
    parser.add_argument("--force-clear", action="store_true",
                        help="Clear any saved progress before starting")
    
    args = parser.parse_args()
    
    # Check if we're using multiple accounts
    multi_account_mode = args.multi_account is not None
    
    if multi_account_mode:
        # Load multiple account credentials
        try:
            with open(args.multi_account, 'r') as f:
                accounts = json.load(f)
            if not accounts:
                print("Error: No accounts found in the provided JSON file")
                return
        except Exception as e:
            print(f"Error loading accounts file: {e}")
            print("Please make sure the file exists and contains valid JSON")
            return
            
        # Initialize multi-account migrator
        migrator = MultiAccountMigrator(accounts)
        
        # Set dry run mode if specified
        if args.dry_run:
            migrator.dry_run = True
            migrator.log_info("DRY RUN MODE: No actual changes will be made")
            
        # Start the migration process with multiple accounts
        migrator.start_time = time.time()
        
        try:
            # Start all accounts
            if not await migrator.start_all():
                return
                
            # Validate source and target groups
            source_chat, source_valid = await migrator.validate_group(args.source)
            if not source_valid:
                migrator.log_error(f"Invalid source group: {args.source}")
                return
                
            target_chat, target_valid = await migrator.validate_group(args.target)
            if not target_valid:
                migrator.log_error(f"Invalid target group: {args.target}")
                return
                
            # Check permissions for all accounts
            await migrator.check_all_permissions(args.target)
            
            # Get members from source group
            filter_bots = args.filter == "active"
            members = await migrator.get_chat_members(args.source, filter_bots=filter_bots, limit=args.limit)
            
            if not members:
                migrator.log_warning("No members found in source group or couldn't retrieve members")
                return
                
            migrator.stats["total"] = len(members)
            
            # Process members directly (multi-account mode doesn't support invite links yet)
            migrator.log_info(f"\n📥 Using multiple accounts for direct addition")
            
            # Split members among accounts and process
            for i, member in enumerate(members):
                if await migrator.add_user(args.target, member.user):
                    migrator.stats["success"] += 1
                else:
                    migrator.stats["failed"] += 1
                    
                # Print progress every 10 users
                if i > 0 and i % 10 == 0:
                    success_rate = (migrator.stats["success"] / i) * 100
                    migrator.log_info(f"Progress: {i}/{len(members)} users processed ({success_rate:.2f}% success rate)")
            
            # Print final statistics
            success_rate = (migrator.stats["success"] / migrator.stats["total"]) * 100 if migrator.stats["total"] > 0 else 0
            
            migrator.log_info("\n📊 Migration Summary:")
            migrator.log_info(f"• Total users processed: {migrator.stats['total']}")
            migrator.log_success(f"• Successfully migrated: {migrator.stats['success']}")
            migrator.log_warning(f"• Failed to migrate: {migrator.stats['failed']}")
            migrator.log_info(f"• Skipped users: {migrator.stats['skipped']}")
            migrator.log_info(f"• Success rate: {success_rate:.2f}%")
            
            duration = time.time() - migrator.start_time
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_formatted = ""
            if hours > 0:
                duration_formatted += f"{int(hours)}h "
            if minutes > 0 or hours > 0:
                duration_formatted += f"{int(minutes)}m "
            duration_formatted += f"{int(seconds)}s"
            
            migrator.log_info(f"• Total time: {duration_formatted}")
            
        except KeyboardInterrupt:
            migrator.log_warning("\nOperation interrupted by user")
            migrator.log_info("Progress saved. Run the same command to resume.")
        except Exception as e:
            migrator.log_error(f"Error during migration: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Disconnect all accounts
            await migrator.stop_all()
            
    else:
        # Original single-account mode
        # Validate required arguments for single account mode
        if not args.api_id or not args.api_hash:
            print("Error: API ID and API hash are required for single account mode")
            return
            
        # Initialize migrator for single account
        migrator = TelegramMigrator(args.api_id, args.api_hash, args.session_name)
        
        # Register signal handlers for graceful shutdown
        migrator.register_signal_handlers()
        
        if args.dry_run:
            migrator.dry_run = True
            migrator.log_info("DRY RUN MODE: No actual changes will be made")
        
        # Handle progress management
        if args.force_clear and os.path.exists(migrator.progress_file):
            os.remove(migrator.progress_file)
            migrator.log_info("Cleared previous progress data")
        
        # Attempt to resume unless explicitly told not to
        resuming = False
        if not args.no_resume:
            resuming = migrator.load_progress()
        
        try:
            # Start timer (unless resuming)
            if not resuming:
                migrator.start_time = time.time()
            else:
                # When resuming, we don't reset the timer completely, but we don't want
                # to count the time the script was not running
                current_time = time.time()
                # Estimate elapsed time based on processed users
                if migrator.stats["total"] > 0:
                    # Rough estimate: if X users took Y time, then total would be (Y/X) * total
                    migrator.start_time = current_time - (current_time - migrator.start_time if hasattr(migrator, 'start_time') else 0)
                else:
                    migrator.start_time = current_time
            
            # Connect to Telegram
            await migrator.start()
            
            # Validate source group
            source_chat, source_valid = await migrator.validate_group(args.source)
            if not source_valid:
                migrator.log_error(f"Invalid source group: {args.source}")
                return
                
            # Validate target group
            target_chat, target_valid = await migrator.validate_group(args.target)
            if not target_valid:
                migrator.log_error(f"Invalid target group: {args.target}")
                return
            
            # Get members from source group if not resuming or not enough users processed
            if not resuming or len(migrator.processed_users) == 0:
                filter_bots = args.filter == "active"
                members = await migrator.get_chat_members(args.source, filter_bots=filter_bots, limit=args.limit)
                
                if not members:
                    migrator.log_warning("No members found in source group or couldn't retrieve members")
                    return
                
                migrator.stats["total"] = len(members)
            else:
                # When resuming, we'll use the loaded progress data and fetch members only for filtering
                members = await migrator.get_chat_members(args.source, filter_bots=(args.filter == "active"), limit=args.limit)
                migrator.log_info(f"Resuming with {len(members)} total members, {len(migrator.processed_users)} already processed")
            
            # Analyze target group for recommendations
            analysis = await migrator.analyze_target_group(args.target)
            
            # Show recommendations and warnings
            if "recommendations" in analysis and analysis["recommendations"]:
                migrator.log_info("\n🔍 Recommendations:")
                for rec in analysis["recommendations"]:
                    migrator.log_info(f"• {rec}")
                    
            if "warnings" in analysis and analysis["warnings"]:
                migrator.log_warning("\n⚠️ Warnings:")
                for warn in analysis["warnings"]:
                    migrator.log_warning(f"• {warn}")
            
            # Choose migration method based on arguments and capabilities
            if args.invite_link:
                # Use invite link approach
                migrator.log_info("\n📤 Using invite link approach.")
                invite_link, sent_count = await migrator.migrate_by_invite_link(
                    args.target, 
                    members,
                    expire_hours=args.expire_hours
                )
                
                if sent_count > 0:
                    migrator.log_success(f"Successfully sent invite link to {sent_count} users")
                else:
                    migrator.log_warning("Failed to send invite links to users")
            else:
                # Use direct addition approach
                migrator.log_info("\n📥 Using direct addition approach")
                
                # Process users in batches
                await migrator.batch_add_users(
                    args.target, 
                    members, 
                    batch_size=args.batch_size, 
                    delay=args.batch_delay
                )
                
                # Retry failed users if needed
                if not args.no_retry and migrator.stats["failed"] > 0:
                    # Get list of failed members that haven't been processed
                    failed_members = [m for m in members if m.user.id not in migrator.processed_users]
                    
                    if failed_members:
                        migrator.log_info(f"Retrying {len(failed_members)} failed users")
                        await migrator.retry_failed_users(args.target, failed_members)
            
            # Generate and save report
            migrator.save_migration_report(source_chat, target_chat)
            
            # Print final statistics
            success_rate = (migrator.stats["success"] / migrator.stats["total"]) * 100 if migrator.stats["total"] > 0 else 0
            
            migrator.log_info("\n📊 Migration Summary:")
            migrator.log_info(f"• Total users processed: {migrator.stats['total']}")
            migrator.log_success(f"• Successfully migrated: {migrator.stats['success']}")
            migrator.log_warning(f"• Failed to migrate: {migrator.stats['failed']}")
            migrator.log_info(f"• Skipped users: {migrator.stats['skipped']}")
            migrator.log_info(f"• Success rate: {success_rate:.2f}%")
            
            duration = time.time() - migrator.start_time
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_formatted = ""
            if hours > 0:
                duration_formatted += f"{int(hours)}h "
            if minutes > 0 or hours > 0:
                duration_formatted += f"{int(minutes)}m "
            duration_formatted += f"{int(seconds)}s"
            
            migrator.log_info(f"• Total time: {duration_formatted}")
            
            # Clean up progress file if completed successfully
            if os.path.exists(migrator.progress_file):
                try:
                    os.remove(migrator.progress_file)
                    migrator.log_info("Progress file removed (migration completed successfully)")
                except:
                    pass
            
        except KeyboardInterrupt:
            migrator.log_warning("\nOperation interrupted by user")
            migrator.save_progress()
            migrator.log_info("Progress saved. Run the same command to resume.")
        except Exception as e:
            migrator.log_error(f"Error during migration: {e}")
            migrator.save_progress()
            import traceback
            traceback.print_exc()
        finally:
            # Disconnect
            await migrator.stop()
        
# Add this at the very end of your file
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation interrupted by user. Progress has been saved.")
        print("Run the same command to resume from where you left off.")
    except Exception as e:
        print(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()