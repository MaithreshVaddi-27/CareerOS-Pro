"""Getting job data in from the outside world.

Everything in this package reaches outside the process to learn about jobs —
source adapters and the career-page scraper discover them, `normalize` turns
provider payloads into rows, `dedup` persists them, and `verification` decides
whether a posting we already hold is still live.

If a module's job is to *talk to an external system about a job*, it belongs
here. If it is to turn rows we already have into a ranked answer for a
candidate, that is `services.matching`.
"""