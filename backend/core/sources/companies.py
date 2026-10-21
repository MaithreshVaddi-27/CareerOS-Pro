# CareerOS-Pro — Company sources for job crawling
#
# Central registry of target employers. Editable without touching
# backend/services/sourcing/company_scraper.py.
#
# Stages:
#   early   — Seed/Series A startups (typically <50 employees)
#   medium  — Series B/C growth companies (typically 50–500 employees)
#   good    — Established tech companies / public market (typically 500+ employees)
#
# mnc        — Large multinational corporations with public career pages

MNC_COMPANIES: list[str] = [
    "Google", "Amazon", "Microsoft", "Apple", "Meta", "Netflix",
    "Uber", "Airbnb", "Salesforce", "Adobe", "Intel", "IBM",
    "Oracle", "Cisco", "NVIDIA", "AMD", "Qualcomm", "LinkedIn",
    "Twitter", "Snap", "Dropbox", "Pinterest", "Spotify",
    "PayPal", "eBay", "HP", "Dell", "Samsung", "Sony",
    "SAP", "VMware", "ServiceNow", "Workday", "Atlassian",
]

STARTUP_COMPANIES_EARLY: list[str] = [
    "Cursor", "Perplexity", "Anthropic", "Mistral", "Cohere",
    "Hugging Face", "Scale AI", "Scale", "LangChain", "Pinecone",
    "Weaviate", "Chroma", "Modular", "Together AI", "Groq",
    "Cerebras", "SambaNova", "Graphcore", "Coveo", "Glean",
    "Retool", "Vercel", "Netlify", "Supabase", "PlanetScale",
    "Temporal", "Confluent", "Databricks", "Snowflake",
]

STARTUP_COMPANIES_MEDIUM: list[str] = [
    "Stripe", "Plaid", "Rippling", "Brex", "Ramp",
    "Notion", "Figma", "Canva", "Miro", "Airtable",
    "HashiCorp", "Elastic", "MongoDB", "Redis", "GitLab",
    "GitHub", "JetBrains", "Jet", "InVision", "Webflow",
    "Contentful", "Sanity", "Strapi", "Storyblok",
]

STARTUP_COMPANIES_GOOD: list[str] = [
    "DoorDash", "Instacart", "Robinhood", "Coinbase",
    "Square", "Block", "Affirm", "Chime", "Revolut",
    "Nubank", "Monzo", "Wise", "Remitly", "Wise",
    "Peloton", "Warby Parker", "Allbirds", "Casper",
    "Rent the Runway", "Stitch Fix", "Farfetch",
]

ALL_COMPANIES: list[str] = (
    MNC_COMPANIES
    + STARTUP_COMPANIES_EARLY
    + STARTUP_COMPANIES_MEDIUM
    + STARTUP_COMPANIES_GOOD
)

# Default boards searched when no company is named in the query.
DEFAULT_JOB_BOARDS: list[str] = [
    "indeed", "glassdoor", "linkedin", "ziprecruiter", "simplyhired",
    "weworkremotely", "wellfound", "remoteok", "workingnomads", "jobspresso",
    "google_jobs",
]

# Major tech company career pages crawled directly.
TECH_COMPANY_CAREER_BOARDS: list[str] = [
    "google_careers", "amazon_jobs", "microsoft_careers", "apple_jobs",
    "meta_careers", "netflix_jobs", "uber_careers", "airbnb_careers",
    "salesforce_careers", "adobe_careers", "intel_careers", "ibm_careers",
    "oracle_careers", "cisco_careers", "nvidia_careers", "amd_careers",
    "qualcomm_careers",
]
