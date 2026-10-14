// The API on the host this page was opened from (port 8000), so the site and #debug also work from a phone
// on the home network (localhost there is the phone).
export const API_BASE = `http://${window.location.hostname}:8000`;
