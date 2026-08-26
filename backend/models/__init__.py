"""Re-export so `from backend.models import Job` keeps working after the
package split (job.py holds the actual SQLAlchemy model)."""
from backend.models.job import Job

__all__ = ["Job"]
