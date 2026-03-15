# SEC EDGAR 10-K Data Finder

## Project Overview
This is a full-stack web application designed to streamline the retrieval of **SEC 10-K filings** and key financial metrics. As a Finance student, I built this tool to automate the tedious process of searching the EDGAR database manually.

## Features
* **Automated Filing Retrieval**: Search by stock ticker or company name to find the most relevant 10-K filings.
* **Financial Data Extraction**: Uses **XBRL** data to pull metrics like Net Income, Revenue, and Total Assets directly into the dashboard.
* **Flexible Date Logic**: Allows for searching filings filed "on or before" or "on or after" specific target dates.
* **Clean Web Interface**: A responsive HTML/CSS frontend designed for ease of use across devices.

## Technical Stack
* **Backend**: Python (Flask)
* **Frontend**: HTML5, CSS3, JavaScript
* **API**: SEC EDGAR (Company Facts & Submissions)
* **Security**: Environment variables used for SEC-compliant User-Agent headers.

## Setup Instructions
1. Clone the repository.
2. Install dependencies: `pip install -r Requirements.txt`.
3. Create a `.env` file with your `SEC_USER_AGENT`.
4. Run `python EDGAR_File.py` to start the local server.
