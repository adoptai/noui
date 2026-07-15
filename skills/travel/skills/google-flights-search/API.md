# Google Flights Search — API

All operations run inside a Tabby browser session (`POST /execute/fetch`).

## search_airports

`POST https://www.google.com/_/FlightsFrontendUi/data/batchexecute` (RPC `H028ib`)

## get_calendar

`POST …/FlightsFrontendService/GetCalendarPicker`

## search_flights

`POST …/FlightsFrontendService/GetShoppingResults`

Entity IDs come from `search_airports` (e.g. `/m/022pfm`). Dates are `YYYY-MM-DD`.
