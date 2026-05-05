# TODO

## Timetable data

- Add timetable fields (`duration_min`, `first_departure`, `last_departure`, `frequency`) to
    connections once the route/stop ordering is stable.

    When doing so, note that durations must be computed in **both directions** from the queried
    station: downstream trips give the forward travel times, but upstream stops require looking at
    the reverse-direction schedules (or the arrival times of the same trips) to produce correct
    durations for stations that come before the origin in the route order.

## Filters

- per type of train

## UI / UX

- put city in the center of the map when clicking on it
- highlight the route when hovering in the list
- show routes on map while the rest is still loading
- put country picker after train or bus picker (if bus, dont show country picker)
- combine research for countries (and remove country picker)

# Bugs

- no trains found for date next month

# Other

- make better referencing
    Output from Opencode:
    These matter more than any HTML change, but require action outside the codebase:
    - Submit to Google Search Console — go to search.google.com/search-console (https://search.google.com/search-console), add your Render URL as a property, and request indexing. Without this, Google may not crawl the site for weeks.
    - Submit a sitemap — for a single-page app a sitemap is just one line. Add this to main.py:
        @app.get("/sitemap.xml")
      def sitemap():
          return Response(
              '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              '<url><loc>https://your-app.onrender.com/</loc></url></urlset>',
              media_type="application/xml"
          )
        Then submit the URL in Search Console.
    - Get inbound links — SEO ranking is mostly about other sites linking to you. Post it on Reddit (r/france, r/train, r/webdev), Hacker News "Show HN", or relevant French communities. A single link from a well-trafficked page beats any amount of meta-tag tuning.
    - Render cold starts — Render's free tier spins down after inactivity, which causes a ~30 s delay on the first visit. Google's crawler may time out and penalise the page. Consider upgrading to a paid tier or using a cron job to ping the site every 10 minutes to keep it warm.

- Add Germany, Spain, Italy, Switzerland, Belgium, Netherdands, Luxembourg