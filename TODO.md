# TODO

## Timetable data

Add timetable fields (`duration_min`, `first_departure`, `last_departure`, `frequency`) to
connections once the route/stop ordering is stable.

When doing so, note that durations must be computed in **both directions** from the queried
station: downstream trips give the forward travel times, but upstream stops require looking at
the reverse-direction schedules (or the arrival times of the same trips) to produce correct
durations for stations that come before the origin in the route order.

## Filters

- per type of train

## UX

- when clicking on a line, remove (or light transparent) all the other lines
- allow english/french
- on the left, group the stops from a same line together. Create dropdown to see all the stops. When click on a stop on the map, the corresponding dropdown should open

## UI

- for small lines, only display the dots when the zoom is sufficient (otherwise, too many dots next to each other)