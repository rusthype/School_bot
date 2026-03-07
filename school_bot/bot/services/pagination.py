from __future__ import annotations


class SchoolPagination:
    def __init__(self, page: int = 1, per_page: int = 10, total_schools: int = 46) -> None:
        self.page = max(1, page)
        self.per_page = max(1, per_page)
        self.total_schools = max(0, total_schools)
        self.total_pages = max(1, (self.total_schools + self.per_page - 1) // self.per_page)
        if self.page > self.total_pages:
            self.page = self.total_pages

    def get_current_numbers(self) -> list[int]:
        start = (self.page - 1) * self.per_page + 1
        end = min(start + self.per_page - 1, self.total_schools)
        if self.total_schools == 0:
            return []
        return list(range(start, end + 1))

    def has_previous(self) -> bool:
        return self.page > 1

    def has_next(self) -> bool:
        return self.page < self.total_pages
