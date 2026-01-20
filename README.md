# Wayback Extractor

A Python tool to create a complete static mirror of a website from the Internet Archive's Wayback Machine. The script downloads HTML pages and all associated assets (CSS, JavaScript, images, PDFs, etc.) from a specific point in time, ensuring consistent styling and navigation.

## Other Options

This was an experiment. This is probably a better tool:
https://github.com/hartator/wayback-machine-downloader/

And there is also this service:
https://www.waybackmachinedownloader.com/en/wayback-and-archive-downloader-pricing/
## Features

- Create a fully functional offline copy of a website as it appeared at a specific date
- Download HTML and all same-site assets (CSS, JS, images, PDFs)
- Rewrite links and asset references to work locally
- Maintain consistent styling across all pages with standardized CSS
- Inject a banner indicating the snapshot source and date
- Generate detailed reports (manifest.json, report.csv, report.md)
- Smart retries and rate limiting to be respectful to the Internet Archive
- Progress tracking with URLs/min statistics
- Prefers the freshest available non-404 Wayback snapshots, falling back gracefully if only 404s exist

## Requirements

- Python 3.8 or higher
- Required Python packages (see `requirements.txt`):
   - `requests`
   - `beautifulsoup4`
   - `lxml`

## Installation

1. Clone this repository or download the script:
   ```bash
   git clone https://github.com/your-username/wayback-extractor.git
   cd wayback-extractor
   ```

2. (Recommended) Create and activate a virtual environment, then install dependencies from `requirements.txt`:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   If you prefer not to use a virtual environment, you can install directly:
   ```bash
   pip install requests beautifulsoup4 lxml
   ```

3. Make the script executable:
   ```bash
   chmod +x wayback_extractor.py
   ```

## Usage

Basic usage:

```bash
python wayback_extractor.py example.org
```

By default this mirrors example.org using today's date (e.g., 2026-01-20) and places the files in a directory named `example.org_YYYYMMDD`. The script automatically picks the newest non-404 snapshot when multiple snapshots exist for a URL, falling back to the newest 404 only if no non-404 snapshots are available.

### Command Line Options

```
python wayback_extractor.py [domain] [options]
```

#### Required Arguments:
- `domain`: The root domain to mirror (e.g., example.org)

#### Optional Arguments:
- `--cutoff YYYY-MM-DD`: Cutoff date (default: today's date)
- `--cutoff-utc-ts YYYYMMDDhhmmss`: Exact timestamp to use instead of cutoff date
- `--outdir DIR`: Output directory (default: domain_YYYYMMDD)
- `--no-subdomains`: Do not include subdomains (default: include them)
- `--strip-all-js`: Remove all JavaScript (default: keep same-domain JS)
- `--no-nonhtml`: Do not include non-HTML files like PDFs (default: include them)
- `--max N`: Maximum number of pages to process (0 = no limit)
- `--path-prefix PATH`: Only include URLs whose path starts with this prefix (e.g., /en/)
- `--rps N`: Requests per second (default: 0.5)
- `--burst N`: Rate limiter burst size (default: 2)
- `--quiet`: Minimal console output
- `--log-assets`: Log each asset download
- `--ignore-query-params`: Ignore URL query parameters when identifying unique URLs
- `--timeout N`: HTTP request timeout in seconds (default: 30)

### Examples

Mirror example.org as of January 1, 2023:
```bash
python wayback_extractor.py example.org --cutoff 2023-01-01
```

Mirror only the English pages:
```bash
python wayback_extractor.py example.org --path-prefix /en/
```

Mirror without JavaScript:
```bash
python wayback_extractor.py example.org --strip-all-js
```

Mirror with a custom output directory:
```bash
python wayback_extractor.py example.org --outdir my-mirror
```

## Output

The script creates a directory structure that mirrors the original website:

```
output_directory/
├── assets/
│   ├── images/
│   ├── javascripts/
│   └── stylesheets/
│       └── application.css
├── en/
│   ├── index.html
│   ├── about/
│   │   └── index.html
│   └── ...
├── manifest.json
├── report.csv
└── report.md
```

### Styling

The script ensures consistent styling across all pages by:
1. Downloading all CSS files referenced by any page
2. Copying the first found CSS to a standard name (`assets/stylesheets/application.css`)
3. Rewriting all HTML pages to use this standardized CSS file with correct relative paths
4. Running a post-processing step to verify and fix any CSS references

### Reports

- `manifest.json`: Detailed information about all mirrored pages and assets
- `report.csv`: CSV summary of all processed URLs and their status
- `report.md`: Markdown report with statistics and any failures

## Troubleshooting

### No CSS or Missing Styles

If some pages are missing styles:
1. Re-run the script to trigger the post-processing step
2. Check that `assets/stylesheets/application.css` exists in your output directory
3. Verify the HTML files have correct relative paths to the CSS file

### Rate Limiting

If you encounter rate limiting from the Internet Archive:
1. Lower the requests per second with `--rps 0.2`
2. Try again later

### Missing Pages

If pages are missing:
1. Check the `report.md` file for failures
2. Try using different cutoff dates as content availability varies

## License

[AGPL License](LICENSE)

## Acknowledgements

This tool relies on the Internet Archive's Wayback Machine. Please be respectful of their service by using reasonable rate limits.
AI was used to generate this.
