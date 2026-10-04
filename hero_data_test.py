#!/usr/bin/env python3
"""
Standalone test to fetch and analyze Deadlock hero data from the official API.
"""

import urllib.request
import json

def fetch_hero_data():
    """Fetch hero data from the Deadlock.io API."""
    try:
        url = "https://deadlock.io/api/v1/heroes.json"
        response = urllib.request.urlopen(url)
        data = response.read()
        return json.loads(data)
    except Exception as e:
        print(f"Error fetching hero data: {e}")
        return None

def main():
    """Main function to process hero data."""
    print("Fetching Deadlock hero data from deadlock.io...")
    
    # Fetch the data
    data = fetch_hero_data()
    
    if not data:
        print("Failed to fetch hero data")
        return
    
    # Get heroes array
    heroes = data.get("heroes", [])
    
    # Print total number of hero records
    total_count = len(heroes)
    print(f"Total hero records returned: {total_count}")
    
    # Print complete raw first hero record for inspection
    if heroes:
        print("\nComplete raw structure of first hero record:")
        print(json.dumps(heroes[0], indent=2))
        
        # Build set of valid hero names using only verified fields
        valid_heroes = set()
        
        for hero in heroes:
            # Check all required conditions
            if (hero.get("playerSelectable", False) and 
                not hero.get("disabled", False) and 
                not hero.get("inDevelopment", False)):
                
                # Only use the displayName.english field
                display_name = hero.get("displayName", {}).get("english")
                if display_name:
                    valid_heroes.add(display_name)
        
        # Print results
        print(f"\nNumber of valid/selectable heroes: {len(valid_heroes)}")
        print("\nComplete sorted list of valid hero names:")
        for name in sorted(valid_heroes):
            print(f"  {name}")
        
        # Check for "Grey Mirage"
        grey_mirage_exists = "Grey Mirage" in valid_heroes
        print(f"\nGrey Mirage exists in valid hero names: {grey_mirage_exists}")

if __name__ == "__main__":
    main()
