# CareerOS-Pro — Website URLs for job boards and company career pages
#
# All URLs are organized by category. Edit here to add/remove sources without
# touching the scraper code.

# ---------------------------------------------------------------------------
# Job board search URL templates
# Each value uses {query} and/or {location} placeholders. Boards are searched
# concurrently when no specific company is named in the query.
# ---------------------------------------------------------------------------

JOB_BOARD_SEARCH_PATTERNS: dict[str, str] = {
    # Major job boards (free, no key required)
    "indeed": "https://www.indeed.com/jobs?q={query}&l={location}",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}&locT=C&locId=1&locKeyword={location}",
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={query}&location={location}",
    "ziprecruiter": "https://www.ziprecruiter.com/candidate/search?search={query}&location={location}",
    "simplyhired": "https://www.simplyhired.com/search?q={query}&l={location}",
    # Remote-first boards
    "weworkremotely": "https://weworkremotely.com/remote-jobs/search?term={query}",
    "wellfound": "https://wellfound.com/role/{query}",
    "remoteok": "https://remoteok.com/remote-{query}-jobs",
    "workingnomads": "https://www.workingnomads.com/jobs?search={query}",
    "jobspresso": "https://jobspresso.co/?s={query}",
    # Google Jobs aggregation (server-rendered)
    "google_jobs": "https://www.google.com/search?q={query}+jobs&ibp=htl;jobs&hl=en&gl=us",
}

# ---------------------------------------------------------------------------
# Direct career page URLs for major companies
#
# Companies with subdomains or non-standard career page structures are mapped
# here so the scraper can fetch them directly instead of guessing /careers.
# Only include domains that resolve reliably — dead domains cause timeouts.
# ---------------------------------------------------------------------------

COMPANY_CAREER_URLS: dict[str, str] = {
    # US tech giants
    "google": "https://careers.google.com/jobs/",
    "alphabet": "https://careers.google.com/jobs/",
    "microsoft": "https://careers.microsoft.com/us/en/search-results",
    "apple": "https://jobs.apple.com/en-us/search",
    "meta": "https://www.metacareers.com/jobs/",
    "facebook": "https://www.metacareers.com/jobs/",
    "amazon": "https://www.amazon.jobs/en/jobs",
    "netflix": "https://jobs.netflix.com/search",
    "uber": "https://www.uber.com/global/en/careers/list/",
    "airbnb": "https://www.airbnb.com/careers/departments",
    # Enterprise software
    "salesforce": "https://salesforce.wd1.myworkdayjobs.com/en-US/External_Career_Page",
    "adobe": "https://ext.adobe.com/jobs/",
    "intel": "https://jobs.intel.com/search",
    "ibm": "https://www.ibm.com/employment/search",
    "oracle": "https://talent.oracle.com/faces/jobs/search",
    "cisco": "https://jobs.cisco.com/jobs/SearchJobs/",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "amd": "https://jobs.amd.com/search",
    "qualcomm": "https://qtr.avature.net/CareerCenter/Search",
    # Finance & payments
    "paypal": "https://www.paypal.com/us/webapps/mpp/careers",
    "ebay": "https://careers.ebay.com/",
    # Hardware & PCs
    "hp": "https://jobs.hp.com/",
    "dell": "https://www.dell.com/en-us/work/dellcareers",
    "samsung": "https://careers.samsung.com/",
    "sony": "https://www.sony.com/en_SonyInfo/careers/",
    # Enterprise software & cloud
    "sap": "https://careers.sap.com/",
    "vmware": "https://careers.vmware.com/",
    "servicenow": "https://careers.servicenow.com/",
    "workday": "https://wd5.myworkdayjob.com/workday",
    "atlassian": "https://www.atlassian.com/company/careers",
    "dropbox": "https://www.dropbox.com/jobs",
    "pinterest": "https://www.pinterest.com/careers/",
    "spotify": "https://www.lifeatspotify.com/jobs/",
}

__all__ = [
    "JOB_BOARD_SEARCH_PATTERNS",
    "COMPANY_CAREER_URLS",
]
