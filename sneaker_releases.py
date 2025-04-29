import cloudscraper
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime, timedelta
import logging
import traceback
import threading
import re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Tuple

# Get the logger from the main bot
logger = logging.getLogger("discord_calendar")

# Configuration constants
JSON_FILE_PATH = "sneaker_releases.json"
FETCH_INTERVAL_HOURS = 6
MAX_PAGES = 10  # Limit number of pages to prevent infinite loops
BASE_URL = "https://www.soleretriever.com/sneaker-release-dates"
DATE_FORMATS = ['%B %d, %Y', '%b %d, %Y', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']

# Cache of compiled date regex patterns
DATE_PATTERNS = [re.compile(r'\b\w+ \d{1,2}, \d{4}\b'), re.compile(r'\b\d{4}-\d{2}-\d{2}\b')]

# State variables
_state = {
    'releases': None,
    'last_fetch_time': None,
    'fetch_lock': threading.Lock(),
    'fetch_in_progress': False,
    'first_run': True
}

@lru_cache(maxsize=32)
def parse_date(date_str: str) -> Optional[datetime.date]:
    """
    Parse a date string using multiple formats with caching for efficiency.
    
    Args:
        date_str: String representation of a date
        
    Returns:
        Parsed date object or None if parsing fails
    """
    if not date_str:
        return None
        
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def load_sneaker_releases_from_file() -> Tuple[Optional[List[Dict[str, Any]]], Optional[datetime]]:
    """
    Load sneaker releases from the JSON file with better error handling.
    
    Returns:
        Tuple of (releases list, last update time) or (None, None) if file doesn't exist/is invalid
    """
    if not os.path.exists(JSON_FILE_PATH):
        logger.info(f"Sneaker releases file {JSON_FILE_PATH} does not exist")
        return None, None
    
    try:
        with open(JSON_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            releases = data.get('releases', [])
            last_updated = data.get('last_updated')
            
            # Parse the last updated timestamp
            last_fetch_time = None
            if last_updated:
                try:
                    last_fetch_time = datetime.fromisoformat(last_updated)
                except ValueError:
                    last_fetch_time = datetime.now()
                
            logger.info(f"Loaded {len(releases)} sneaker releases from file")
            return releases, last_fetch_time
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading sneaker releases from file: {e}")
        return None, None

def should_fetch_new_data() -> bool:
    """
    Determine if we should fetch new data based on the last fetch time
    
    Returns:
        True if we should fetch new data, False otherwise
    """
    # Always fetch on first run
    if _state['first_run']:
        _state['first_run'] = False
        return True
    
    # If we've never fetched data, we should fetch
    if not _state['last_fetch_time']:
        return True
    
    # If the last fetch was more than FETCH_INTERVAL_HOURS ago, we should fetch
    time_since_last_fetch = datetime.now() - _state['last_fetch_time']
    return time_since_last_fetch > timedelta(hours=FETCH_INTERVAL_HOURS)

def parse_sneaker_item(item: BeautifulSoup) -> Dict[str, Any]:
    """
    Parse a single sneaker item from the HTML.
    
    Args:
        item: BeautifulSoup object representing a sneaker item
        
    Returns:
        Dictionary containing sneaker information
    """
    # Extract link
    link_elem = item.find('a')
    link = link_elem['href'] if link_elem else None
    
    # Extract name
    name_elem = item.find('div', class_='line-clamp-2')
    name = name_elem.text.strip() if name_elem else None
    
    # Extract date
    date_elem = item.find('span', class_='text-gray-600')
    release_date = date_elem.text.strip() if date_elem else None
    
    # Extract price and SKU
    price_elem = item.find('p', class_='text-sm')
    price = None
    sku = None
    if price_elem:
        price_text = price_elem.text.strip()
        parts = price_text.split('•')
        price = parts[0].strip() if parts else None
        sku = parts[1].strip() if len(parts) > 1 else None
    
    # Check if trending
    is_trending = bool(item.find('div', attrs={'title': 'Trending now'}))
    
    return {
        'name': name,
        'release_date': release_date,
        'price': price,
        'sku': sku,
        'link': f"https://www.soleretriever.com{link}" if link else None,
        'is_trending': is_trending
    }

def fetch_page(scraper: cloudscraper.CloudScraper, page_num: int) -> List[Dict[str, Any]]:
    """
    Fetch and parse a single page of sneaker releases.
    
    Args:
        scraper: CloudScraper instance
        page_num: Page number to fetch
        
    Returns:
        List of sneaker items from the page
    """
    url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
    logger.debug(f"Fetching page {page_num}...")
    
    try:
        response = scraper.get(url, timeout=15)
        
        if response.status_code == 404:
            logger.info(f"Page {page_num} not found.")
            return []
            
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if page exists by looking for raffle items
            raffle_items = soup.find_all('div', attrs={'data-test-id': 'raffle-item'})
            
            if not raffle_items:
                logger.info(f"No items found on page {page_num}.")
                return []
            
            logger.debug(f"Found {len(raffle_items)} items on page {page_num}")
            return [parse_sneaker_item(item) for item in raffle_items]
        
        logger.warning(f"Failed to get page {page_num}. Status code: {response.status_code}")
        return []
            
    except Exception as e:
        logger.error(f"Error fetching page {page_num}: {e}")
        return []

def fetch_sneaker_data() -> List[Dict[str, Any]]:
    """
    Core function to fetch sneaker data from the website.
    
    Returns:
        List of sneaker items
    """
    logger.info("Starting background fetch of sneaker releases")
    
    # Create a scraper instance
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'darwin',
            'mobile': False
        }
    )
    
    all_sneakers = []
    
    try:
        # Fetch pages concurrently using a thread pool
        with ThreadPoolExecutor(max_workers=3) as executor:
            # Start with page 1 and see if we need to fetch more
            results = list(executor.map(
                lambda p: fetch_page(scraper, p), 
                range(1, MAX_PAGES + 1)
            ))
            
            # Flatten results and filter out empty pages
            for page_results in results:
                if page_results:
                    all_sneakers.extend(page_results)
                else:
                    # Stop when we hit an empty page
                    break
        
        if all_sneakers:
            # Sort releases by date
            all_sneakers.sort(key=lambda x: x.get('release_date', ''))
            logger.info(f"Successfully fetched {len(all_sneakers)} sneaker releases")
        else:
            logger.warning("No sneaker releases found")
        
        return all_sneakers
        
    except Exception as e:
        logger.error(f"Error in fetch_sneaker_data: {e}")
        logger.error(f"Fetch error details: {traceback.format_exc()}")
        return []

def save_sneaker_data(releases: List[Dict[str, Any]]) -> bool:
    """
    Save sneaker data to file.
    
    Args:
        releases: List of sneaker release data
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Save to a single JSON file
        data = {
            'total_releases': len(releases),
            'trending_releases': sum(1 for s in releases if s.get('is_trending', False)),
            'releases': releases,
            'last_updated': datetime.now().isoformat()
        }
        
        with open(JSON_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Results saved to {JSON_FILE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Error saving sneaker releases to file: {e}")
        return False

def get_sneaker_releases(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetch sneaker releases with improved caching and concurrency control.
    
    Args:
        force_refresh: Whether to force a refresh even if we have recent data
        
    Returns:
        List of sneaker release dictionaries
    """
    # Try to load from file if we don't have data in memory
    if not _state['releases']:
        releases, last_fetch_time = load_sneaker_releases_from_file()
        if releases:
            _state['releases'] = releases
            _state['last_fetch_time'] = last_fetch_time
    
    # Return cached data if it's recent enough and not forcing refresh
    if (not force_refresh and 
        not _state['first_run'] and 
        _state['releases'] and 
        _state['last_fetch_time'] and
        (datetime.now() - _state['last_fetch_time']) < timedelta(hours=FETCH_INTERVAL_HOURS)):
        logger.info(f"Using cached sneaker releases data from {_state['last_fetch_time']}")
        return _state['releases']
    
    # Return cached data if a fetch is already in progress
    if _state['fetch_in_progress']:
        logger.info("Sneaker releases fetch already in progress, returning cached data")
        return _state['releases'] or []
    
    # Try to acquire the lock for fetching
    if not _state['fetch_lock'].acquire(blocking=False):
        logger.warning("Could not acquire lock for sneaker releases fetch")
        return _state['releases'] or []
    
    # Start a background thread for fetching
    def fetch_thread():
        try:
            _state['fetch_in_progress'] = True
            
            # Fetch the data
            all_sneakers = fetch_sneaker_data()
            
            if all_sneakers:
                # Save data to file
                if save_sneaker_data(all_sneakers):
                    # Update state
                    _state['releases'] = all_sneakers
                    _state['last_fetch_time'] = datetime.now()
            
        except Exception as e:
            logger.error(f"Error in fetch thread: {e}")
            logger.error(f"Fetch thread error details: {traceback.format_exc()}")
        finally:
            _state['fetch_in_progress'] = False
            _state['fetch_lock'].release()
    
    # Start the fetch in a background thread
    thread = threading.Thread(target=fetch_thread)
    thread.daemon = True
    thread.start()
    
    # Return whatever data we have, even if it's old
    return _state['releases'] or []

def get_upcoming_sneaker_releases(days: int = 7) -> List[Dict[str, Any]]:
    """
    Get sneaker releases for the next specified number of days.
    
    Args:
        days: Number of days to look ahead
        
    Returns:
        List of sneaker releases for the next days
    """
    # Try to load from file if we don't have data in memory
    if not _state['releases']:
        releases, last_fetch_time = load_sneaker_releases_from_file()
        if releases:
            _state['releases'] = releases
            _state['last_fetch_time'] = last_fetch_time
    
    # Check if we should fetch new data
    if should_fetch_new_data():
        logger.info("Data is outdated or this is the first run, triggering a background fetch")
        get_sneaker_releases()
    
    # If still no data, return empty list
    if not _state['releases']:
        return []
    
    # Get current date
    today = datetime.now().date()
    end_date = today + timedelta(days=days)
    
    # Filter releases for the next days and only include trending releases
    upcoming_releases = []
    for release in _state['releases']:
        try:
            # Only include trending releases
            if not release.get('is_trending', False):
                continue
                
            # Get and parse the release date
            date_str = release.get('release_date', '')
            release_date = parse_date(date_str)
            
            if release_date and today <= release_date <= end_date:
                upcoming_releases.append(release)
        except Exception as e:
            logger.error(f"Error processing release {release.get('name')}: {e}")
    
    return upcoming_releases

def format_sneaker_release(release: Dict[str, Any], date_format: str = "%d/%m/%Y") -> str:
    """
    Format a sneaker release for display.
    
    Args:
        release: Sneaker release data
        date_format: Format for the date
        
    Returns:
        Formatted sneaker release string
    """
    name = release.get('name', 'Unknown')
    link = release.get('link', '')
    
    # Return the name as a hyperlink if a link is available
    return f"[{name}]({link})" if link else name 