package httpapi

import (
	"embed"
	"net/http"
)

// The console is intentionally available only on the configured, loopback-
// restricted local inference gateway. Health-only network-facing container
// deployments never register these routes.
//
//go:embed console/index.html console/app.css console/app.js
var consoleFiles embed.FS

func registerLocalConsole(mux *http.ServeMux) {
	const csp = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; object-src 'none'; form-action 'none'; frame-ancestors 'none'"
	serve := func(asset, mediaType string) http.HandlerFunc {
		return func(w http.ResponseWriter, _ *http.Request) {
			data, err := consoleFiles.ReadFile("console/" + asset)
			if err != nil {
				http.Error(w, "Console asset unavailable", http.StatusInternalServerError)
				return
			}
			w.Header().Set("Content-Security-Policy", csp)
			w.Header().Set("Referrer-Policy", "no-referrer")
			w.Header().Set("X-Frame-Options", "DENY")
			w.Header().Set("Content-Type", mediaType)
			_, _ = w.Write(data)
		}
	}
	mux.HandleFunc("GET /console", serve("index.html", "text/html; charset=utf-8"))
	mux.HandleFunc("GET /console/app.css", serve("app.css", "text/css; charset=utf-8"))
	mux.HandleFunc("GET /console/app.js", serve("app.js", "text/javascript; charset=utf-8"))
}
