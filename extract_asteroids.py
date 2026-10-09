import argparse # Read command-line options, such as --pages 5 and --start-page 50
import getpass # Enter the NASA API key without displaying it on screen
import json # Parse JSON responses and save data as JSON files
import os # Read the NASA API key from the NASA_API_KEY environment variable
import time # Pause between requests and before retrying failed requests
from datetime import datetime, timezone # Generate a UTC timestamp to name each extraction folder
from pathlib import Path # Manage file paths, create folders, and write files
from urllib.error import HTTPError, URLError # Handle HTTP errors, such as 429, and connection errors
from urllib.parse import urlencode # Encode request parameters for the URL, such as page=0&size=20
from urllib.request import urlopen # Send a request to the NASA API and read the response


BASE_URL = "https://api.nasa.gov/neo/rest/v1/neo/browse" # The NASA API endpoint for browsing near-Earth objects (asteroids). The API requires an API key.


# Fetch one page of asteroids using the API key and page number
def fetch_page(api_key, page):
    # Encode the request parameters for the URL
    query = urlencode({
        "api_key": api_key,  # NASA API key
        "page": page,        # Page number, starting from 0
        "size": 20,          # Request 20 asteroids per page
    })

    # Allow up to 3 attempts: attempt values are 0, 1, and 2
    for attempt in range(3):
        try:
            # Send the request with a 60-second timeout
            # The connection closes automatically when leaving this block
            with urlopen(f"{BASE_URL}?{query}", timeout=60) as response:
                # Parse the JSON response and return the Python data
                return json.load(response)

        # Handle HTTP errors returned by the API
        except HTTPError as error:
            # Stop immediately if the API request limit is reached
            if error.code == 429:
                raise RuntimeError(
                    "NASA request limit reached. Try again later."
                ) from None

            # Stop for HTTP errors below 500 or after the final attempt
            # Otherwise, retry server errors (HTTP 500 and above)
            if error.code < 500 or attempt == 2:
                raise RuntimeError(
                    f"NASA returned HTTP {error.code}."
                ) from None

        # Handle connection errors and request timeouts
        except (URLError, TimeoutError):
            # Stop if the third attempt also fails
            if attempt == 2:
                raise RuntimeError(
                    "Connection failed after 3 attempts."
                ) from None

        # Wait 2 seconds before the second attempt and 4 before the third
        time.sleep(2 ** (attempt + 1))


# Remove all fields named "links" from nested data
def remove_links(value):
    # If the value is a dictionary, process each field
    if isinstance(value, dict):
        return {
            # Keep the field and clean its nested content
            key: remove_links(item)
            for key, item in value.items()
            # Skip "links" fields because URLs may contain the API key
            if key != "links"
        }

    # If the value is a list, clean every item inside it
    if isinstance(value, list):
        return [remove_links(item) for item in value]

    # Return other values, such as strings and numbers, unchanged
    return value


# Manage the complete extraction process
def main():
    # Create a parser to read command-line options
    parser = argparse.ArgumentParser(
        description="Extract NASA asteroid catalogue pages."
    )

    # Number of pages to extract; default is 1
    parser.add_argument("--pages", type=int, default=1)

    # First page to extract; default is 0
    parser.add_argument("--start-page", type=int, default=0)

    # Read the options provided when running the script
    args = parser.parse_args()

    # Reject invalid page counts and starting page numbers
    if args.pages < 1 or args.start_page < 0:
        parser.error("--pages must be positive; --start-page cannot be negative.")

    # Read the API key from the environment and remove surrounding whitespace
    api_key = os.environ.get("NASA_API_KEY", "").strip()

    # If no environment key exists, ask for it without displaying it
    if not api_key:
        api_key = getpass.getpass("NASA API key (hidden): ").strip()

    # Stop if the API key is still empty
    if not api_key:
        parser.error("NASA API key is required.")

    # Generate a UTC timestamp to identify this extraction run
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")

    # Find the folder containing this Python file
    project_root = Path(__file__).resolve().parent

    # Build the output path: data/raw/<run timestamp>
    output_dir = project_root / "data" / "raw" / run_id

    # Create the output folder and any missing parent folders
    # Stop if this exact run folder already exists
    output_dir.mkdir(parents=True, exist_ok=False)

    # Track unique asteroid IDs to avoid counting duplicates
    asteroid_ids = set()

    # Track the number of successfully saved pages
    saved_pages = 0

    try:
        # Loop through the requested page numbers
        for page in range(args.start_page, args.start_page + args.pages):
            # Fetch one page from NASA
            payload = fetch_page(api_key, page)

            # Read pagination details from the response
            metadata = payload["page"]
            total_pages = metadata["total_pages"]

            # Check that the requested page is within the catalogue
            if page >= total_pages:
                raise RuntimeError(
                    f"Page {page} is outside the catalogue. "
                    f"Available pages: 0 to {total_pages - 1}."
                )

            # Display catalogue totals once, before saving the first page
            if saved_pages == 0:
                print(f"Catalogue asteroids: {metadata['total_elements']:,}")
                print(f"Catalogue pages: {total_pages:,}")

            # Get the asteroid records from this page
            asteroids = payload["near_earth_objects"]

            # Create a filename such as page_00000.json
            target = output_dir / f"page_{page:05d}.json"

            # Remove navigation links and save the response as readable JSON
            # Preserve Unicode characters and use 2 spaces for indentation
            target.write_text(
                json.dumps(remove_links(payload), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Add this page's asteroid IDs to the unique ID collection
            asteroid_ids.update(item["id"] for item in asteroids)

            # Update the saved page count and display progress
            saved_pages += 1
            print(f"Page {page}: saved {len(asteroids)} asteroids")

            # Stop if this was the final page in the catalogue
            if page + 1 >= total_pages:
                print("Reached the end of the catalogue.")
                break

            # Wait 1 second if more pages are requested
            if saved_pages < args.pages:
                time.sleep(1)

    # Handle request failures, invalid values, and missing response fields
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"Extraction stopped: {error}")

        # Exit with status 1 to indicate failure
        raise SystemExit(1)

    # Display a summary whether extraction succeeds or fails
    finally:
        print(f"Saved pages: {saved_pages}")
        print(f"Distinct asteroids saved: {len(asteroid_ids):,}")
        print(f"Output folder: {output_dir}")


if __name__ == "__main__":
    main()
