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

## UI / UX

- in search bar results, if value in () is the same as previous value, dont put the ()

# Bugs

- dont allow date picker before today