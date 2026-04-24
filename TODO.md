# TODO

## Use data from multiple days
Since routes can change from one day to another, check available routes for different dates.

- a whole month?
- a week during next month?

## Timetable data

Add timetable fields (`duration_min`, `first_departure`, `last_departure`, `frequency`) to
connections once the route/stop ordering is stable.

When doing so, note that durations must be computed in **both directions** from the queried
station: downstream trips give the forward travel times, but upstream stops require looking at
the reverse-direction schedules (or the arrival times of the same trips) to produce correct
durations for stations that come before the origin in the route order.

## Filters

- per type of train

## Interactions

- when clicking on a line, remove (or light transparent) all the other lines