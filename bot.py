"""
Discord Calendar Bot
A simple Discord bot for managing server-specific calendars.

Features:
- Add and view events with date and time
- Daily calendar updates in a designated channel
- Support for different timezones and time formats
- Role-based permissions

Author: github/kaymeer
License: GNU General Public License v3.0
Version: 1.1.0
"""

# TODO: Tagging dates with: today, tomorrow, next week, next month, next year
# TODO: Limit a server to only 100 events at a time to prevent abuse
# TODO: Add a command to clear all events for a specific date
# TODO: Add a command to delete an event
# TODO: Add a command to edit an event
# TODO: Add a command to view all events for a specific date
# TODO: Add a command to view all events for a specific month
# TODO: Add a command to view all events for a specific year

import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import asyncio
import logging
import traceback
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from discord.ext.commands import cooldown, BucketType
from logging.handlers import RotatingFileHandler
import sys

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create a file handler with rotation (10 MB max size, keeping 5 backup files)
file_handler = RotatingFileHandler(
    filename="bot.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(log_formatter)

# Create console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

# Configure the root logger
logger = logging.getLogger("discord_calendar")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Silence noisy loggers
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

logger.info("Logger initialized")

# Load environment variables
load_dotenv()
logger.debug("Environment variables loaded")

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Date format options
DATE_FORMATS = {
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "YYYY-MM-DD": "%Y-%m-%d"
}

# Time format options
TIME_FORMATS = {
    "24h": "%H:%M",  # 24-hour format (14:30)
    "12h": "%I:%M %p"  # 12-hour format (02:30 PM)
}

# Rate limiting
RATE_LIMIT = 3  # commands per minute
RATE_LIMIT_PER = 60  # seconds

# Database setup
def init_db():
    """
    Initialize the database with required tables.
    
    Creates two tables if they don't exist:
    - events: Stores all calendar events
    - server_settings: Stores server-specific configuration
    """
    logger.info("Initializing database...")
    conn = sqlite3.connect('calendar.db')
    c = conn.cursor()
    
    # Create tables for events and server settings
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  guild_id INTEGER,
                  title TEXT NOT NULL,
                  event_date TEXT NOT NULL,
                  event_time TEXT,
                  created_by INTEGER,
                  created_timezone TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS server_settings
                 (guild_id INTEGER PRIMARY KEY,
                  admin_role_id INTEGER,
                  update_channel_id INTEGER,
                  update_time TEXT,
                  update_days INTEGER DEFAULT 7,
                  date_format TEXT DEFAULT 'DD/MM/YYYY',
                  time_format TEXT DEFAULT '24h',
                  timezone TEXT DEFAULT 'UTC')''')
    
    # Check if the created_timezone column exists, and add it if not
    try:
        c.execute("SELECT created_timezone FROM events LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, add it
        c.execute("ALTER TABLE events ADD COLUMN created_timezone TEXT")
        logger.info("Added created_timezone column to events table")
    
    conn.commit()
    conn.close()
    logger.info("Database initialization complete")

def get_db():
    """
    Get a database connection with proper error handling.
    
    Returns:
        A connection to the SQLite database
        
    Raises:
        sqlite3.Error: If the database connection fails
    """
    try:
        return sqlite3.connect('calendar.db', timeout=20)
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        logger.debug(f"Connection error details: {traceback.format_exc()}")
        raise

async def execute_db_query(query, params=None):
    """
    Execute a database query with proper connection handling.
    
    This async function ensures that connections are properly closed,
    even if an error occurs.
    
    Args:
        query: SQL query string
        params: Parameters for the query (optional)
        
    Returns:
        Cursor object after executing the query
        
    Raises:
        sqlite3.Error: If the database query fails
    """
    logger.debug(f"Executing query: {query} with params: {params}")
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if params:
            c.execute(query, params)
        else:
            c.execute(query)
        conn.commit()
        return c
    except sqlite3.Error as e:
        logger.error(f"Database query error: {e}")
        logger.debug(f"Query execution error details: {traceback.format_exc()}")
        raise
    finally:
        if conn:
            conn.close()
            logger.debug("Database connection closed")

def format_date(date_str: str, format_str: str) -> str:
    """
    Convert date string to the specified format.
    
    Args:
        date_str: Date string in one of the supported formats
        format_str: Target format string for displaying the date
        
    Returns:
        Formatted date string
        
    Raises:
        ValueError: If the date cannot be parsed or formatted
    """
    try:
        # Try different input formats
        for fmt in DATE_FORMATS.values():
            try:
                date = datetime.strptime(date_str, fmt)
                return date.strftime(format_str)
            except ValueError:
                continue
        raise ValueError("Invalid date format")
    except Exception as e:
        raise ValueError(f"Error formatting date: {str(e)}")

def format_time(time_str: str, format_str: str, timezone: str = 'UTC') -> str:
    """
    Convert time string to the specified format and timezone.
    
    Args:
        time_str: Time string in 24-hour format (HH:MM)
        format_str: Target format string for displaying the time
        timezone: Timezone name to convert the time to
        
    Returns:
        Formatted time string
        
    Raises:
        ValueError: If the time cannot be parsed or formatted
    """
    if not time_str:
        return ""
    
    try:
        # Parse the time string (assuming 24h format input)
        hour, minute = map(int, time_str.split(':'))
        
        # Start with UTC time
        utc_now = datetime.now(pytz.UTC)
        # Create time today with the specified hour/minute
        time_utc = utc_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Convert to specified timezone
        tz = pytz.timezone(timezone)
        local_time = time_utc.astimezone(tz)
        
        # Format according to preference
        return local_time.strftime(format_str)
    except Exception as e:
        logger.error(f"Error formatting time: {e}")
        return time_str  # Return original if conversion fails

async def is_admin(interaction: discord.Interaction) -> bool:
    """
    Check if the user has admin permissions.
    
    This function will check if either:
    1. The user has server administrator permissions
    2. The user has the designated admin role for calendar commands
    
    Args:
        interaction: The Discord interaction object
        
    Returns:
        True if the user has admin permissions, False otherwise
    """
    if interaction.user.guild_permissions.administrator:
        return True
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT admin_role_id FROM server_settings WHERE guild_id = ?', (interaction.guild_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return any(role.id == result[0] for role in interaction.user.roles)
    return False

@bot.event
async def on_ready():
    logger.info(f'Bot is ready! Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guilds serving approximately {sum(g.member_count for g in bot.guilds)} users')
    
    # Log guild details at debug level
    for guild in bot.guilds:
        logger.debug(f"Connected to guild: {guild.name} (ID: {guild.id}) with {guild.member_count} members")
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
        logger.debug(f"Command sync error details: {traceback.format_exc()}")
    
    # Start scheduled tasks
    if not daily_update.is_running():
        daily_update.start()
        logger.info("Daily update task started")
    
    if not cleanup_old_events.is_running():
        cleanup_old_events.start()
        logger.info("Cleanup task for old events started")

@bot.event
async def on_guild_join(guild):
    """Log when the bot joins a new guild"""
    logger.info(f"Joined new guild: {guild.name} (ID: {guild.id}) with {guild.member_count} members")
    logger.debug(f"Guild details - Owner: {guild.owner.name} (ID: {guild.owner.id}), Region: {guild.region if hasattr(guild, 'region') else 'Unknown'}")

@bot.event
async def on_guild_remove(guild):
    """Log when the bot is removed from a guild"""
    logger.info(f"Removed from guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    logger.error(f"Error in {event}: {args} {kwargs}")
    logger.error(f"Error details: {traceback.format_exc()}")

@bot.tree.command(
    name="calendar_set_permission_role",
    description="Set which role can use calendar commands (requires server administrator permissions)"
)
async def set_permission_role(interaction: discord.Interaction, role: discord.Role):
    """Set which role can use calendar commands"""
    # Log command invocation
    logger.info(f"Command 'calendar_set_permission_role' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not interaction.user.guild_permissions.administrator:
        logger.warning(f"Unauthorized calendar_set_permission_role attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # First try to update existing settings
        c.execute('''UPDATE server_settings 
                     SET admin_role_id = ?
                     WHERE guild_id = ?''',
                  (role.id, interaction.guild_id))
        
        # If no row was updated, insert a new one
        if c.rowcount == 0:
            c.execute('''INSERT INTO server_settings (guild_id, admin_role_id)
                         VALUES (?, ?)''',
                      (interaction.guild_id, role.id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Permission role set to '{role.name}' (ID: {role.id}) by user {interaction.user.id} in guild {interaction.guild_id}")
        
        await interaction.response.send_message(f"Permission role set to {role.mention}", ephemeral=True)
    except Exception as e:
        logger.error(f"Error setting permission role by user {interaction.user.id} in guild {interaction.guild_id}: {e}")
        await interaction.response.send_message(
            "An error occurred while setting the permission role. Please try again later.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_set_daily_update",
    description="Enable daily updates in a channel. Updates will continue until disabled"
)
async def set_daily_update(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    time: str,
    days: int = 7
):
    """Configure daily update settings"""
    # Log command invocation
    logger.info(f"Command 'calendar_set_daily_update' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_set_daily_update attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    try:
        # Validate time format
        datetime.strptime(time, "%H:%M")
        
        if days < 1 or days > 30:
            await interaction.response.send_message("Days must be between 1 and 30.", ephemeral=True)
            return
        
        conn = get_db()
        c = conn.cursor()
        
        # Get the server's timezone
        c.execute('''SELECT timezone FROM server_settings WHERE guild_id = ?''', (interaction.guild_id,))
        result = c.fetchone()
        timezone = result[0] if result and result[0] else "UTC"
        
        # First try to update existing settings
        c.execute('''UPDATE server_settings 
                     SET update_channel_id = ?, update_time = ?, update_days = ?
                     WHERE guild_id = ?''',
                  (channel.id, time, days, interaction.guild_id))
        
        # If no row was updated, insert a new one
        if c.rowcount == 0:
            c.execute('''INSERT INTO server_settings 
                         (guild_id, update_channel_id, update_time, update_days)
                         VALUES (?, ?, ?, ?)''',
                      (interaction.guild_id, channel.id, time, days))
        
        conn.commit()
        logger.info(f"Daily updates configured for guild {interaction.guild_id}: channel={channel.id}, time={time}, days={days}")
        
        # Get settings after update for verification
        c.execute('''SELECT * FROM server_settings WHERE guild_id = ?''', (interaction.guild_id,))
        settings = c.fetchone()
        logger.debug(f"Updated settings for guild {interaction.guild_id}: {settings}")
        
        conn.close()
        
        await interaction.response.send_message(
            f"Daily updates configured for {channel.mention} at {time} ({timezone}), showing the next {days} days.",
            ephemeral=True
        )
    except ValueError:
        logger.warning(f"Invalid time format '{time}' attempted by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message(
            "Invalid time format. Please use HH:MM format (24-hour time).",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error setting daily update for guild {interaction.guild_id}: {e}")
        await interaction.response.send_message(
            "An error occurred while setting up daily updates.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_disable_daily_update",
    description="Disable daily updates for this server"
)
async def disable_daily_update(interaction: discord.Interaction):
    """Disable daily updates"""
    # Log command invocation
    logger.info(f"Command 'calendar_disable_daily_update' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_disable_daily_update attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''UPDATE server_settings 
                 SET update_channel_id = NULL, update_time = NULL
                 WHERE guild_id = ?''',
              (interaction.guild_id,))
    
    conn.commit()
    conn.close()
    
    logger.info(f"Daily updates disabled for guild {interaction.guild_id}")
    await interaction.response.send_message("Daily updates have been disabled.", ephemeral=True)

@bot.tree.command(
    name="calendar_add_event",
    description="Add a new event to the calendar"
)
@app_commands.checks.cooldown(RATE_LIMIT, RATE_LIMIT_PER)
async def add_event(
    interaction: discord.Interaction,
    title: str,
    day: int,
    month: int,
    year: int,
    time: str = None
):
    """Add a new event to the calendar"""
    # Log command invocation
    logger.info(f"Command 'calendar_add_event' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_add_event attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    try:
        # Validate date components
        if not (1 <= day <= 31 and 1 <= month <= 12 and year >= datetime.now().year):
            logger.warning(f"Invalid date components (day={day}, month={month}, year={year}) provided by user {interaction.user.id} in guild {interaction.guild_id}")
            raise ValueError("Invalid date components")
        
        # Create date string in YYYY-MM-DD format for storage
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        
        # Get server's preferences
        conn = get_db()
        c = conn.cursor()
        c.execute('''SELECT date_format, time_format, timezone 
                     FROM server_settings 
                     WHERE guild_id = ?''', (interaction.guild_id,))
        result = c.fetchone()
        date_format = result[0] if result and result[0] else "DD/MM/YYYY"
        time_format = result[1] if result and result[1] else "24h"
        timezone = result[2] if result and result[2] else "UTC"
        
        # Validate time format if provided
        if time:
            try:
                datetime.strptime(time, "%H:%M")
            except ValueError:
                logger.warning(f"Invalid time format '{time}' provided by user {interaction.user.id} in guild {interaction.guild_id}")
                raise ValueError("Invalid time format. Please use HH:MM format (e.g., 14:30 for 2:30 PM).")
        
        # Store the event (time is already in server's timezone, no conversion needed)
        c.execute('''INSERT INTO events (guild_id, title, event_date, event_time, created_by, created_timezone)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (interaction.guild_id, title, date_str, time, interaction.user.id, timezone))
        conn.commit()
        conn.close()
        
        logger.info(f"Event '{title}' added by user {interaction.user.id} in guild {interaction.guild_id} for date {date_str} {time if time else '(all day)'}")
        
        # Format date and time for display - when displaying the time, don't convert timezone
        # since the input time is already in the server's timezone
        display_date = format_date(date_str, DATE_FORMATS[date_format])
        time_str = ""
        if time:
            # Display the time as-is without timezone conversion
            formatted_time = datetime.strptime(time, "%H:%M").strftime(TIME_FORMATS[time_format])
            time_str = f" at {formatted_time}"
        
        await interaction.response.send_message(
            f"Event '{title}' added successfully for {display_date}{time_str}!",
            ephemeral=True
        )
    except ValueError as e:
        logger.warning(f"Event creation error by user {interaction.user.id} in guild {interaction.guild_id}: {str(e)}")
        await interaction.response.send_message(
            f"Error: {str(e)}. Please provide valid date components (day: 1-31, month: 1-12, year: current or future).",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Unexpected error during event creation by user {interaction.user.id} in guild {interaction.guild_id}: {str(e)}")
        await interaction.response.send_message(
            "An unexpected error occurred while adding your event. Please try again later.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_view",
    description="View upcoming events"
)
@app_commands.checks.cooldown(RATE_LIMIT, RATE_LIMIT_PER)
async def view_calendar(interaction: discord.Interaction, days: int = 7):
    """View upcoming events for the specified number of days"""
    # Log command invocation
    logger.info(f"Command 'calendar_view' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_view attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if not 1 <= days <= 365:
        logger.warning(f"Invalid days parameter ({days}) provided by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("Please specify a number of days between 1 and 365.", ephemeral=True)
        return
    
    logger.info(f"Calendar view request for {days} days by user {interaction.user.id} in guild {interaction.guild_id}")
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Get server's preferences
        c.execute('''SELECT date_format, time_format, timezone 
                     FROM server_settings 
                     WHERE guild_id = ?''', (interaction.guild_id,))
        result = c.fetchone()
        date_format = result[0] if result and result[0] else "DD/MM/YYYY"
        time_format = result[1] if result and result[1] else "24h"
        current_timezone = result[2] if result and result[2] else "UTC"
        
        # Get all events with their creation dates and timezones
        c.execute('''SELECT e.title, e.event_date, e.event_time, e.created_timezone
                     FROM events e
                     WHERE e.guild_id = ? 
                     AND e.event_date >= date('now')
                     AND e.event_date <= date('now', '+' || ? || ' days')
                     ORDER BY e.event_date, e.event_time''',
                  (interaction.guild_id, days))
        events = c.fetchall()
        conn.close()
        
        if not events:
            logger.info(f"No events found for next {days} days in guild {interaction.guild_id}")
            await interaction.response.send_message(f"No upcoming events in the next {days} days.", ephemeral=True)
            return
        
        logger.info(f"Found {len(events)} events for next {days} days in guild {interaction.guild_id}")
        
        embed = discord.Embed(title=f"Upcoming Events (Next {days} days)", color=discord.Color.blue())
        embed.description = f"**Timezone:** {current_timezone}"
        embed.set_footer(text="Developed by github/kaymeer")
        
        # Get the server's timezone
        server_tz = pytz.timezone(current_timezone)
        
        # Group events by date
        events_by_date = {}
        for title, date, time_str, created_timezone in events:
            # Skip processing for all-day events
            if not time_str:
                display_date = format_date(date, DATE_FORMATS[date_format])
                if display_date not in events_by_date:
                    events_by_date[display_date] = []
                events_by_date[display_date].append((title, "00:00", "All day"))
                continue
            
            try:
                # Parse the time string
                hour, minute = map(int, time_str.split(':'))
                
                # Create a datetime object from the event date and time
                event_date_obj = datetime.strptime(date, "%Y-%m-%d")
                
                # If we have the creation timezone, use it for proper conversion
                if created_timezone:
                    # Create the datetime in the original timezone
                    original_tz = pytz.timezone(created_timezone)
                    event_datetime = original_tz.localize(datetime(
                        year=event_date_obj.year,
                        month=event_date_obj.month,
                        day=event_date_obj.day,
                        hour=hour,
                        minute=minute
                    ))
                    
                    # Convert to the current server timezone
                    event_datetime_localized = event_datetime.astimezone(server_tz)
                else:
                    # Fallback if no creation timezone: treat it as if it was in the current timezone
                    event_datetime_localized = server_tz.localize(datetime(
                        year=event_date_obj.year,
                        month=event_date_obj.month,
                        day=event_date_obj.day,
                        hour=hour,
                        minute=minute
                    ))
                
                # Format the date for display
                display_date = format_date(date, DATE_FORMATS[date_format])
                
                # Format the time according to the server's preferred format
                formatted_time = event_datetime_localized.strftime(TIME_FORMATS[time_format])
                
                # Add to the events by date dictionary
                if display_date not in events_by_date:
                    events_by_date[display_date] = []
                
                events_by_date[display_date].append((title, time_str, formatted_time))
                
            except Exception as e:
                # Fallback if timezone conversion fails
                logger.error(f"Error converting time for event in guild {interaction.guild_id}: {e}")
                display_date = format_date(date, DATE_FORMATS[date_format])
                if display_date not in events_by_date:
                    events_by_date[display_date] = []
                
                # Fallback to basic formatting
                formatted_time = datetime.strptime(time_str, "%H:%M").strftime(TIME_FORMATS[time_format])
                events_by_date[display_date].append((title, time_str, formatted_time))
        
        # Add each date as a field with all events for that date, sorted by time
        for date, day_events in events_by_date.items():
            # Sort by the original time (the second element in the tuple)
            day_events.sort(key=lambda x: x[1] if x[1] else "00:00")
            
            event_list = []
            for title, _, formatted_time in day_events:
                event_list.append(f"**{formatted_time}** - {title}")
            
            embed.add_field(
                name=f"{date}",
                value="\n".join(event_list),
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error displaying calendar for user {interaction.user.id} in guild {interaction.guild_id}: {e}")
        await interaction.response.send_message(
            "An error occurred while retrieving your calendar events. Please try again later.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_set_date_format",
    description="Set the preferred date format for the server"
)
async def set_date_format(interaction: discord.Interaction, format_name: str):
    """Set the preferred date format for the server"""
    # Log command invocation
    logger.info(f"Command 'calendar_set_date_format' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_set_date_format attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if format_name not in DATE_FORMATS:
        logger.warning(f"Invalid date format '{format_name}' attempted by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message(
            f"Invalid format. Please choose from: {', '.join(DATE_FORMATS.keys())}",
            ephemeral=True
        )
        return
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # First try to update existing settings
        c.execute('''UPDATE server_settings 
                     SET date_format = ?
                     WHERE guild_id = ?''',
                  (format_name, interaction.guild_id))
        
        # If no row was updated, insert a new one
        if c.rowcount == 0:
            c.execute('''INSERT INTO server_settings (guild_id, date_format)
                         VALUES (?, ?)''',
                      (interaction.guild_id, format_name))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Date format set to '{format_name}' by user {interaction.user.id} in guild {interaction.guild_id}")
        
        await interaction.response.send_message(
            f"Date format set to {format_name}",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error setting date format by user {interaction.user.id} in guild {interaction.guild_id}: {e}")
        await interaction.response.send_message(
            "An error occurred while setting the date format. Please try again later.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_set_time_format",
    description="Set the preferred time format (12-hour or 24-hour) for the server"
)
async def set_time_format(interaction: discord.Interaction, format_name: str):
    """Set the preferred time format for the server"""
    # Log command invocation
    logger.info(f"Command 'calendar_set_time_format' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_set_time_format attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if format_name not in TIME_FORMATS:
        logger.warning(f"Invalid time format '{format_name}' attempted by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message(
            f"Invalid format. Please choose from: {', '.join(TIME_FORMATS.keys())}",
            ephemeral=True
        )
        return
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # First try to update existing settings
        c.execute('''UPDATE server_settings 
                     SET time_format = ?
                     WHERE guild_id = ?''',
                  (format_name, interaction.guild_id))
        
        # If no row was updated, insert a new one
        if c.rowcount == 0:
            c.execute('''INSERT INTO server_settings (guild_id, time_format)
                         VALUES (?, ?)''',
                      (interaction.guild_id, format_name))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Time format set to '{format_name}' by user {interaction.user.id} in guild {interaction.guild_id}")
        
        await interaction.response.send_message(
            f"Time format set to {format_name}",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error setting time format by user {interaction.user.id} in guild {interaction.guild_id}: {e}")
        await interaction.response.send_message(
            "An error occurred while setting the time format. Please try again later.",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_set_timezone",
    description="Set the server's timezone"
)
async def set_timezone(interaction: discord.Interaction, timezone: str):
    """Set the server's timezone"""
    # Log command invocation
    logger.info(f"Command 'calendar_set_timezone' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_set_timezone attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    try:
        # Validate timezone
        pytz.timezone(timezone)
        
        conn = get_db()
        c = conn.cursor()
        
        # First try to update existing row
        c.execute('''UPDATE server_settings 
                     SET timezone = ?
                     WHERE guild_id = ?''',
                  (timezone, interaction.guild_id))
        
        # If no row was updated, insert a new one
        if c.rowcount == 0:
            c.execute('''INSERT INTO server_settings 
                         (guild_id, timezone, date_format, time_format, update_days)
                         VALUES (?, ?, 'DD/MM/YYYY', '24h', 7)''',
                      (interaction.guild_id, timezone))
        
        conn.commit()
        logger.info(f"Timezone for guild {interaction.guild_id} set to {timezone}")
        
        conn.close()
        
        await interaction.response.send_message(
            f"Timezone set to {timezone}. Daily updates will now use this timezone.",
            ephemeral=True
        )
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Invalid timezone '{timezone}' attempted by {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message(
            "Invalid timezone. Please use a valid timezone name (e.g., 'America/New_York', 'Europe/London', 'Asia/Tokyo').",
            ephemeral=True
        )

@bot.tree.command(
    name="calendar_delete_event",
    description="Delete an event from the calendar"
)
@app_commands.checks.cooldown(RATE_LIMIT, RATE_LIMIT_PER)
async def delete_event(
    interaction: discord.Interaction,
    title: str,
    day: int,
    month: int,
    year: int
):
    """Delete an event from the calendar"""
    # Log command invocation
    logger.info(f"Command 'calendar_delete_event' invoked by {interaction.user.id} in guild {interaction.guild_id}")
    
    if not await is_admin(interaction):
        logger.warning(f"Unauthorized calendar_delete_event attempt by user {interaction.user.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    try:
        # Validate date components
        if not (1 <= day <= 31 and 1 <= month <= 12 and year >= datetime.now().year):
            logger.warning(f"Invalid date components (day={day}, month={month}, year={year}) provided by user {interaction.user.id} in guild {interaction.guild_id}")
            raise ValueError("Invalid date components")
        
        # Create date string in YYYY-MM-DD format for storage
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        
        conn = get_db()
        c = conn.cursor()
        
        # Get server's date format for display
        c.execute('''SELECT date_format FROM server_settings WHERE guild_id = ?''', (interaction.guild_id,))
        result = c.fetchone()
        date_format = result[0] if result and result[0] else "DD/MM/YYYY"
        
        # Delete the event
        c.execute('''DELETE FROM events 
                     WHERE guild_id = ? 
                     AND title = ? 
                     AND event_date = ?''',
                  (interaction.guild_id, title, date_str))
        
        deleted_count = c.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count == 0:
            logger.warning(f"No event found with title '{title}' on {date_str} in guild {interaction.guild_id}")
            await interaction.response.send_message(
                f"No event found with title '{title}' on {format_date(date_str, DATE_FORMATS[date_format])}.",
                ephemeral=True
            )
        else:
            logger.info(f"Deleted event '{title}' on {date_str} in guild {interaction.guild_id}")
            await interaction.response.send_message(
                f"Successfully deleted event '{title}' on {format_date(date_str, DATE_FORMATS[date_format])}.",
                ephemeral=True
            )
            
    except ValueError as e:
        logger.warning(f"Event deletion error by user {interaction.user.id} in guild {interaction.guild_id}: {str(e)}")
        await interaction.response.send_message(
            f"Error: {str(e)}. Please provide valid date components (day: 1-31, month: 1-12, year: current or future).",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Unexpected error during event deletion by user {interaction.user.id} in guild {interaction.guild_id}: {str(e)}")
        await interaction.response.send_message(
            "An unexpected error occurred while deleting your event. Please try again later.",
            ephemeral=True
        )

@tasks.loop(minutes=1)
async def daily_update():
    """
    Check and send daily updates for all configured servers.
    
    This task runs every minute and checks if it's time to send
    daily calendar updates for any servers. For each server with
    daily updates enabled, it will:
    
    1. Get the server's timezone and update time
    2. Check if the current time matches the update time
    3. Fetch upcoming events for the next X days
    4. Format and send the events in a nicely formatted embed
    
    All times are stored in UTC but displayed in the server's timezone.
    """
    now = datetime.now(pytz.UTC)  # Get current time in UTC
    logger.debug(f"Running daily update check at {now} UTC")
    
    conn = get_db()
    c = conn.cursor()
    
    # Get all servers with configured updates
    c.execute('''SELECT * FROM server_settings 
                 WHERE update_channel_id IS NOT NULL 
                 AND update_time IS NOT NULL''')
    servers = c.fetchall()
    
    logger.debug(f"Found {len(servers)} servers with configured daily updates")
    
    # Get column names for better access
    column_names = [description[0] for description in c.description]
    
    for server in servers:
        # Convert to dict for easier access
        server_dict = {column_names[i]: server[i] for i in range(len(column_names))}
        guild_id = server_dict['guild_id']
        channel_id = server_dict['update_channel_id']
        update_time = server_dict['update_time']
        update_days = server_dict['update_days']
        timezone = server_dict['timezone']
        
        try:
            # Get server's timezone, defaulting to UTC if None
            tz = pytz.timezone(timezone if timezone else 'UTC')
            
            # Convert current UTC time to server's timezone
            server_time = now.astimezone(tz)
            
            # Parse update time in server's timezone
            hour, minute = map(int, update_time.split(':'))
            # Create update time in server's timezone
            update_datetime = tz.localize(datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0))
            
            logger.debug(f"Guild {guild_id}: Server time is {server_time}, update time is {hour}:{minute}")
            
            # Check if it's time to send the update
            if server_time.hour == hour and server_time.minute == minute:
                logger.info(f"Sending daily update for guild {guild_id}")
                
                # Get upcoming events for this server
                c.execute('''SELECT title, event_date, event_time, created_timezone FROM events
                             WHERE guild_id = ?
                             AND DATE(event_date) >= DATE('now')
                             AND DATE(event_date) <= DATE('now', '+' || ? || ' days')
                             ORDER BY event_date, event_time''',
                          (guild_id, update_days))
                events = c.fetchall()
                
                logger.debug(f"Guild {guild_id}: Found {len(events)} events for the next {update_days} days")
                
                # Get server settings for formatting
                c.execute('''SELECT date_format, time_format FROM server_settings
                             WHERE guild_id = ?''',
                          (guild_id,))
                settings = c.fetchone()
                date_format = settings[0] if settings and settings[0] else 'DD/MM/YYYY'
                time_format = settings[1] if settings and settings[1] else '24h'
                
                # Get the channel to send the update to
                guild = bot.get_guild(guild_id)
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        # Format events by date
                        events_by_date = {}
                        
                        # Full date format for display
                        date_fmt = DATE_FORMATS[date_format]
                        time_fmt = TIME_FORMATS[time_format]
                        
                        for title, date_str, time_str, event_tz in events:
                            # Use the stored timezone for the event, or server timezone if not available
                            event_timezone = event_tz if event_tz else timezone
                            
                            # Format date for display
                            date_for_key = format_date(date_str, date_fmt)
                            
                            if date_for_key not in events_by_date:
                                events_by_date[date_for_key] = []
                            
                            # Format time if it exists
                            formatted_time = ""
                            if time_str:
                                try:
                                    # Try to convert time to server's timezone if needed
                                    formatted_time = format_time(time_str, time_fmt, timezone)
                                except:
                                    # If conversion fails, use the original time string
                                    formatted_time = time_str
                            else:
                                # Set "All day" for events with no time
                                formatted_time = "All day"
                            
                            # Store the original time for sorting
                            events_by_date[date_for_key].append((title, time_str, formatted_time))
                        
                        # Create embed for calendar view
                        embed = discord.Embed(
                            title=f"Calendar Updates - Next {update_days} Days",
                            description=f"**Timezone:** {timezone}",
                            color=0x3498db,
                        )
                        
                        embed.set_footer(text="Developed by github/kaymeer")
                        
                        # Add each date as a field with all events for that date, sorted by time
                        for date, day_events in events_by_date.items():
                            # Sort by the original time (the second element in the tuple)
                            day_events.sort(key=lambda x: x[1] if x[1] else "00:00")
                            
                            event_list = []
                            for title, _, formatted_time in day_events:
                                event_list.append(f"**{formatted_time}** - {title}")
                            
                            embed.add_field(
                                name=f"{date}",
                                value="\n".join(event_list),
                                inline=False
                            )
                        
                        await channel.send(embed=embed)
                        logger.info(f"Daily update sent for guild {guild_id}")
                    else:
                        logger.warning(f"Channel {channel_id} not found for guild {guild_id}")
                else:
                    logger.warning(f"Guild {guild_id} not found")
        except Exception as e:
            logger.error(f"Error sending daily update for guild {guild_id}: {e}")
            logger.error(f"Update error details: {traceback.format_exc()}")
    
    conn.close()
    logger.debug("Daily update check completed")

@tasks.loop(hours=24)
async def cleanup_old_events():
    """
    Cleanup task to delete events that have passed more than 7 days ago.
    
    This task runs once per day and removes old events from the database
    to prevent it from growing indefinitely.
    """
    try:
        logger.info("Running cleanup task for old events")
        conn = get_db()
        c = conn.cursor()
        
        # Delete events older than 7 days
        c.execute('''DELETE FROM events 
                     WHERE DATE(event_date) < DATE('now', '-7 days')''')
        
        deleted_count = c.rowcount
        conn.commit()
        conn.close()
        
        logger.info(f"Cleanup task completed: {deleted_count} old events removed")
    except Exception as e:
        logger.error(f"Error during event cleanup task: {e}")
        logger.error(f"Cleanup error details: {traceback.format_exc()}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors"""
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"This command is on cooldown. Try again in {error.retry_after:.1f}s",
            ephemeral=True
        )
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
    else:
        logger.error(f"Command error in guild {interaction.guild_id} by user {interaction.user.id}: {error}")
        logger.error(f"Command error details: {traceback.format_exc()}")
        await interaction.response.send_message(
            "An error occurred while executing this command.",
            ephemeral=True
        )

# Initialize database
init_db()

# Run the bot
logger.info("Starting Discord Calendar Bot...")
try:
    # Start the bot - this will start the event loop
    bot.run(os.getenv('DISCORD_TOKEN'))
except Exception as e:
    logger.critical(f"Failed to start bot: {e}")
    logger.critical(f"Startup error details: {traceback.format_exc()}")
finally:
    logger.info("Bot has shut down") 