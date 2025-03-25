# Discord Calendar Bot

A Discord bot that helps manage calendars for your server. Users can add events, view upcoming events, and receive daily updates.
If you do now want to set this up yourself, feel free to use [this one](https://discord.com/oauth2/authorize?client_id=1353654752663175198).

## Features

- Add events with title, date, and optional time using slash commands
- View upcoming events for a specified number of days
- Daily updates for upcoming events (configurable days range)
- Server-specific calendar data
- Role-based permissions
- Persistent storage using SQLite
- Rate limiting to prevent abuse
- Multiple date format options
- 12-hour and 24-hour time format support
- Timezone support for global servers
- Automatic cleanup of events that have passed more than 7 days ago

## Required Bot Permissions

When adding the bot to your server, it needs the following permissions:

### Required Permissions
- `Send Messages` - To send calendar updates and responses
- `Embed Links` - To display calendar events in formatted embeds
- `View Channels` - To view channels where it needs to send messages

## Commands

- `/add_event <title> <day> <month> <year> [time]` - Add a new event to the calendar
- `/view_calendar [days]` - View upcoming events (default 7 days, max 365 days, requires calendar permissions)
- `/set_daily_update <channel> <time> [days]` - Enable daily updates in a channel. Updates will continue until disabled. Days parameter can be 1-30 (default 7)
- `/disable_daily_update` - Disable daily updates for this server
- `/set_admin_role <role>` - Set which role can use calendar commands (requires server administrator permissions)
- `/set_date_format <format>` - Set the preferred date format
- `/set_time_format <format>` - Set the preferred time format (12-hour or 24-hour)
- `/set_timezone <timezone>` - Set the server's timezone

## Date and Time Formats

### Date Formats
The bot supports three date formats:
- DD/MM/YYYY (default)
- MM/DD/YYYY
- YYYY-MM-DD

### Time Formats
The bot supports two time formats:
- 24-hour format (default)
- 12-hour format

### Timezones
The bot supports all standard timezone names (e.g., 'America/New_York', 'Europe/London', 'Asia/Tokyo').
Default timezone is UTC.

To set your server's timezone, use `/set_timezone` with a valid timezone name.

**Timezone Handling**:
- When adding an event with `/add_event`, the time you provide is treated as being in your server's current timezone
- The bot records which timezone was active when each event was created
- When you change timezones, existing events will be properly converted to show the correct time in the new timezone
- For example, if you add an event at 11:00 in Europe/London and later change to Europe/Amsterdam, the event will show at 12:00 (which is the equivalent time in Amsterdam)

This ensures that events always display at the intended time regardless of timezone changes.

Common timezone names:
- America/New_York
- America/Los_Angeles
- Europe/London
- Europe/Amsterdam
- Asia/Tokyo
- Asia/Shanghai
- Australia/Sydney

## Admin Configuration

Server administrators can:
- Set which role has access to calendar commands
- Configure daily update channel and time
- Choose between 1-30 days for daily update periods (default is 7 days)
- Set the preferred date format
- Set the preferred time format (12-hour or 24-hour)
- Set the server's timezone

## Permissions

The bot uses a two-tier permission system:
1. Server Administrator permissions (for setting up the bot)
2. Calendar permissions (for using calendar features)

To set up the bot:
1. Server administrators must use `/set_admin_role` to designate which role can use calendar commands
2. Users with that role or administrator permissions can then use all calendar features

## Rate Limiting

To prevent abuse, the bot implements rate limiting:
- 3 commands per minute per user
- Maximum of 365 days for calendar views

## Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file with your Discord bot token:
   ```
   DISCORD_TOKEN=your_bot_token_here
   ```
4. Run the bot:
   ```bash
   python bot.py
   ```

## Data Storage

All calendar data is stored in a SQLite database (`calendar.db`) and is server-specific. Make sure to:
- Back up the database file regularly
- Keep sufficient disk space available
- Monitor the database size

The bot includes automatic maintenance:
- Events that have dates more than 7 days in the past are automatically deleted
- This cleanup process runs once per day
- This prevents the database from growing indefinitely over time

## Logging

The bot logs important events, errors, and information to both the console and a log file (`bot.log`):
- INFO level: Regular operations like startup, command execution, and daily updates
- WARNING level: Issues that don't prevent operation but may need attention
- ERROR level: Problems that affect functionality

## Support

If you encounter any issues or need help:
1. Check the error messages in the console and log files
2. Ensure all dependencies are installed correctly
3. Verify your Discord bot token is valid
4. Check if the bot has the necessary permissions in your server
5. Make sure you have the required role permissions
6. You can contact me here for questions: [Discord server](https://discord.gg/2tuTNZacau)

## Security

- Only users with the designated role or administrator permissions can use calendar commands
- Rate limiting prevents command spam
- Database connections are properly managed and closed
- Error handling prevents crashes and data corruption

## Contributing

Contributions are welcome! If you'd like to contribute to this project:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature-name`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add some feature'`)
5. Push to the branch (`git push origin feature/your-feature-name`)
6. Open a Pull Request

Please ensure your code follows the existing style and includes appropriate comments and documentation.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
