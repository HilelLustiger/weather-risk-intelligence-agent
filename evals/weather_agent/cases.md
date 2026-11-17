# Weather-risk agent eval - proposed cases (for review)

Ground truth is computed from the DB / archive at grading time, so "expected" below describes
what is checked, not a fixed value. `update_scores` is stubbed in every case (no DB writes).
Cases marked (seed) are the ones you wrote.

| id | category | question | expected |
|---|---|---|---|
| r01 | ranking | Which 3 hubs are most at risk overall? | the actual top-3 hubs by score |
| r02 | ranking (seed) | What are the most dangerous hubs in the Midwest? | top Midwest hubs by score; no hub from another region |
| r03 | ranking | Which hub in the Northeast has the highest risk score? | the top Northeast hub |
| r04 | ranking | Rank the 5 riskiest hubs in the West. | top-5 West hubs, in order |
| r05 | ranking | מה המרכז הכי מסוכן באזור הדרום? | the top South hub (Hebrew question) |
| r06 | ranking | Which region has the highest average risk score? | the region with the highest avg score |
| n01 | numbers | What is the risk score of the Texas hub? | TX score (+-0.1) |
| n02 | numbers (seed) | Why is the risk at the Arizona hub so high? | names AZ's dominant component with its exact count |
| n03 | numbers (seed) | Why is North Dakota's risk score so high? | names ND's dominant component with its exact count |
| n04 | numbers | Compare the risk of the Texas and California hubs. | both scores correct; says which is higher |
| n05 | numbers | How many extreme snow days did the Colorado hub have? | CO extreme snow count (3-year window), states the >= 5 cm threshold |
| n06 | numbers (seed) | Compare the South and the Midwest in terms of storm risk. | storm-day totals for both regions correct; says which is higher |
| n07 | numbers | Which has more storm days, the Northeast or the West? | correct region, with both totals |
| h01 | history (seed) | How many days did it snow at the Minnesota hub last year? | MN snow_days for last year from the archive |
| h02 | history | How many rainy days did the Florida hub have in 2024? | FL rain_days for 2024 |
| h03 | history | How many frost days were there at the Maine hub in 2025? | ME frost_days for 2025 |
| s01 | scope | What's the weather risk at our Houston hub? | says there is no Houston hub; points to the TX hub (Austin) |
| s02 | scope | How exposed is our Miami hub? | says there is no Miami hub; points to the FL hub (Tallahassee) |
| s03 | scope | Will there be a storm in Chicago this week? | says short-term forecasts are out of scope; no invented forecast |
| s04 | scope | What's the risk score of the Puerto Rico hub? | says there is no such hub; no invented score |
| s05 | scope | How many days did it snow at the Texas hub in 1900? | says the data isn't available for that year; no invented number |
| t01 | tools | Give me the most up-to-date risk ranking. | returns the current ranking from the DB (no refresh tool exists; data is refreshed daily) |
| t02 | tools | When were the most recent extreme weather days at the North Dakota hub? | dates match ND's latest extreme days (the 50-request loop case) |
| t03 | tools | What is the riskiest hub right now? | correct top hub |
| t04 | tools (2 turns) | 1: Which Midwest hub is the riskiest? 2: And why? | turn 2 explains the hub from turn 1 using its component counts |

Applied to every case (not a separate question): the run finishes within the 10-request limit.
`update_scores` was removed from the agent (scores refresh daily in the background).
