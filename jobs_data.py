"""Job listing data source for JobSwiper.

Every caller downstream (the /api/jobs route, the draft-generation
prompt, the swiper UI) only ever consumes the plain dict shape returned
by get_jobs()/get_job_by_id() below -- never anything mock-specific.
That's deliberate: swapping this file's two functions for real calls
to the Careerjet affiliate API (https://www.careerjet.com/partners/api/)
is the only change needed to go live with real listings. Careerjet's
search endpoint returns fields that map directly onto this shape
(title, company/salary as free text, url, locations) -- the "email"
field has no Careerjet equivalent and would stay "" for every real
listing until/unless a future source provides one, which the swiper's
existing "no email listed" path already handles.
"""

MOCK_JOBS = [
    {
        "id": "sa-001",
        "title": "Junior Sales Associate",
        "company": "Mr Price",
        "location": "Cape Town, Western Cape",
        "salary": "R6,500 - R8,000 / month",
        "description": "Help customers find what they need on the floor, work the till, and keep the store looking sharp. No experience required -- full training given. Retail hours, including some weekends.",
        "posted_at": "2026-07-14",
        "email": "recruitment@mrpricecapetown.example",
        "url": "https://www.careerjet.co.za/jobad/example-mr-price-sales-associate",
    },
    {
        "id": "sa-002",
        "title": "Junior Frontend Developer",
        "company": "Yoco",
        "location": "Cape Town, Western Cape (Hybrid)",
        "salary": "R25,000 - R35,000 / month",
        "description": "Join our product team building the dashboard South African small businesses use every day. React, TypeScript. We care more about how you think than your CV -- a strong portfolio or GitHub matters more than a degree.",
        "posted_at": "2026-07-15",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-yoco-frontend-dev",
    },
    {
        "id": "sa-003",
        "title": "Call Centre Agent",
        "company": "Discovery",
        "location": "Sandton, Gauteng",
        "salary": "R7,200 / month + incentives",
        "description": "Handle inbound client queries for our health plans. Matric required, clear credit and criminal record. Full product training provided in your first two weeks.",
        "posted_at": "2026-07-12",
        "email": "callcentre.jobs@discovery.example",
        "url": "https://www.careerjet.co.za/jobad/example-discovery-call-centre",
    },
    {
        "id": "sa-004",
        "title": "Warehouse Assistant",
        "company": "Takealot",
        "location": "Johannesburg, Gauteng",
        "salary": "R6,800 / month",
        "description": "Pick, pack, and dispatch customer orders in a fast-paced fulfilment centre. Shift work, on your feet most of the day. Physically fit, reliable, punctual.",
        "posted_at": "2026-07-10",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-takealot-warehouse",
    },
    {
        "id": "sa-005",
        "title": "Junior Bookkeeper",
        "company": "Sage",
        "location": "Durban, KwaZulu-Natal (Remote)",
        "salary": "R12,000 - R16,000 / month",
        "description": "Support our accounts team with invoicing, reconciliations, and data capture on Sage Accounting. Studying towards or holding a bookkeeping/accounting qualification preferred, not required.",
        "posted_at": "2026-07-16",
        "email": "earlycareers@sage.example",
        "url": "https://www.careerjet.co.za/jobad/example-sage-junior-bookkeeper",
    },
    {
        "id": "sa-006",
        "title": "Waitron",
        "company": "Vida e Caffè",
        "location": "Pretoria, Gauteng",
        "salary": "R4,500 / month + tips",
        "description": "Front-of-house at one of our busiest branches -- taking orders, serving customers, keeping the till honest. Friendly, energetic, available weekends.",
        "posted_at": "2026-07-11",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-vida-waitron",
    },
    {
        "id": "sa-007",
        "title": "IT Support Intern",
        "company": "Standard Bank",
        "location": "Johannesburg, Gauteng",
        "salary": "R9,500 / month (12-month internship)",
        "description": "First-line desktop and network support for internal staff under our graduate internship programme. IT diploma or degree (completed or final year) required.",
        "posted_at": "2026-07-13",
        "email": "graduateprogramme@standardbank.example",
        "url": "https://www.careerjet.co.za/jobad/example-standard-bank-it-intern",
    },
    {
        "id": "sa-008",
        "title": "Social Media Coordinator",
        "company": "Woolworths",
        "location": "Cape Town, Western Cape",
        "salary": "R14,000 - R18,000 / month",
        "description": "Plan and post daily content across Instagram, TikTok, and X for our food and lifestyle brand. A portfolio of your own content (personal or freelance) counts more than formal experience.",
        "posted_at": "2026-07-09",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-woolworths-social-media",
    },
    {
        "id": "sa-009",
        "title": "Junior Electrician",
        "company": "Eskom",
        "location": "Polokwane, Limpopo",
        "salary": "R11,000 - R14,000 / month",
        "description": "Assist qualified electricians on maintenance and installation work across our regional grid. Trade test or relevant N-qualification required. Own transport to site an advantage.",
        "posted_at": "2026-07-08",
        "email": "artisan.recruitment@eskom.example",
        "url": "https://www.careerjet.co.za/jobad/example-eskom-junior-electrician",
    },
    {
        "id": "sa-010",
        "title": "Data Capturer",
        "company": "Old Mutual",
        "location": "Cape Town, Western Cape (Remote)",
        "salary": "R6,000 / month",
        "description": "Accurately capture policy and claims data into our internal systems. Fast, accurate typing, comfortable with repetitive detail-focused work, own laptop and stable internet.",
        "posted_at": "2026-07-15",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-old-mutual-data-capturer",
    },
    {
        "id": "sa-011",
        "title": "Junior Graphic Designer",
        "company": "Takealot",
        "location": "Cape Town, Western Cape (Hybrid)",
        "salary": "R16,000 - R22,000 / month",
        "description": "Design banners, product imagery, and campaign creative for our marketing team. Figma and Adobe Suite. Send us a portfolio link -- that's what we actually look at first.",
        "posted_at": "2026-07-16",
        "email": "creative.careers@takealot.example",
        "url": "https://www.careerjet.co.za/jobad/example-takealot-junior-designer",
    },
    {
        "id": "sa-012",
        "title": "Security Officer",
        "company": "Fidelity Services Group",
        "location": "Durban, KwaZulu-Natal",
        "salary": "R6,200 / month",
        "description": "Access control and patrol duties at a corporate site. Valid PSIRA grade C or higher, clear criminal record, own transport to shifts.",
        "posted_at": "2026-07-07",
        "email": "",
        "url": "https://www.careerjet.co.za/jobad/example-fidelity-security-officer",
    },
]


def get_jobs(exclude_ids=None):
    """All available listings, minus any the caller already knows about
    (hidden or already applied to). Real pagination/search params would
    hang off this same function once it's backed by Careerjet."""
    exclude_ids = exclude_ids or set()
    return [j for j in MOCK_JOBS if j["id"] not in exclude_ids]


def get_job_by_id(job_id):
    return next((j for j in MOCK_JOBS if j["id"] == job_id), None)
