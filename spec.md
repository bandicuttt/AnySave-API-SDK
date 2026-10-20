{
  "api": {
    "name": "Downloader API — Search Module",
    "version": "1.0.0",
    "base_url": "https://your-downloader-api.example.com",
    "description": "Music search module: Deezer/iTunes metadata search, YouTube search via yt-dlp, and Deezer charts. Designed to be composed with the existing /downloader/* endpoints to form a complete music search → download pipeline.",
    "authentication": {
      "type": "Bearer",
      "header": "Authorization",
      "format": "Bearer <API_TOKEN>",
      "description": "All search endpoints require a valid API token. Token is bound to a portal subscription that determines plan access and usage token balance.",
      "errors": {
        "missing_header": {
          "status": 401,
          "code": "unauthorized",
          "detail": "Missing Authorization header"
        },
        "invalid_scheme": {
          "status": 401,
          "code": "unauthorized",
          "detail": "Invalid Authorization scheme. Expected: Bearer <token>"
        },
        "empty_token": {
          "status": 401,
          "code": "unauthorized",
          "detail": "Empty Bearer token"
        },
        "invalid_token": {
          "status": 401,
          "code": "unauthorized",
          "detail": "Invalid API token"
        }
      }
    },
    "response_envelope": {
      "success": {
        "shape": {
          "status": "ok",
          "data": "<endpoint-specific object>"
        },
        "description": "All successful responses are wrapped in this envelope. The actual response payload is always inside 'data'."
      },
      "error": {
        "shape": {
          "status": "error",
          "error": {
            "code": "<machine_readable_code>",
            "detail": "<human_readable_description>",
            "extra": "<optional object with diagnostic data>"
          }
        },
        "description": "All error responses (4xx and 5xx) use this envelope. 'extra' is present for validation errors (contains field-level details)."
      }
    },
    "rate_limiting": {
      "description": "Token-based rate limiting with per-endpoint hard caps. Effective limit = min(plan_rate_limit, endpoint_hard_cap).",
      "response_headers": {
        "X-RateLimit-Limit": "Effective requests-per-minute limit",
        "X-RateLimit-Remaining": "Requests remaining in current window",
        "X-RateLimit-Reset": "Unix timestamp when the window resets"
      },
      "exceeded_response": {
        "status": 429,
        "code": "rate_limit_exceeded",
        "detail": "Rate limit exceeded. Try again in <N> seconds",
        "additional_headers": {
          "Retry-After": "Seconds until window reset"
        }
      }
    },
    "billing": {
      "description": "Each endpoint consumes usage tokens from the active subscription balance. Fixed cost per request (no refunds on failure for search endpoints).",
      "insufficient_tokens_response": {
        "status": 403,
        "code": "insufficient_usage_tokens",
        "detail": "Not enough usage tokens: need <X>, have <Y>"
      },
      "no_subscription_response": {
        "status": 403,
        "code": "subscription_required",
        "detail": "Active subscription required for token consumption"
      },
      "plan_access_denied_response": {
        "status": 403,
        "code": "access_forbidden",
        "detail": "Your plan '<plan>' does not allow endpoint '<endpoint_code>'"
      }
    },
    "endpoints": [
      {
        "id": "search_music",
        "method": "POST",
        "path": "/search/music",
        "endpoint_code": "search.music",
        "summary": "Search for music tracks by text query",
        "description": "Searches for music tracks via Deezer API with automatic fallback to iTunes when Deezer returns 0 results or fails with 5xx. On 429/403 from Deezer, the orchestrator automatically retries once with a proxy before falling back to iTunes. Each result contains a pre-built 'yt_query' field intended for subsequent /search/youtube calls.",
        "plan_access": ["free", "lite", "pro"],
        "token_cost": 1,
        "rate_limit": {
          "hard_cap_per_minute": 60,
          "effective_formula": "min(plan_limit, 60)"
        },
        "request": {
          "content_type": "application/json",
          "body_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
              "query": {
                "type": "string",
                "min_length": 1,
                "max_length": 200,
                "description": "Search query (track name, artist, or combination). Whitespace is stripped automatically."
              },
              "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "default": 10,
                "description": "Maximum number of results to return per page"
              },
              "cursor": {
                "type": "string",
                "nullable": true,
                "default": null,
                "description": "Opaque cursor from previous response's 'next_cursor' for pagination. Pass null (or omit) for the first page."
              }
            }
          },
          "example": {
            "query": "The Weeknd Blinding Lights",
            "limit": 10,
            "cursor": null
          }
        },
        "responses": {
          "200": {
            "description": "Search completed successfully (may return empty results)",
            "schema": {
              "status": "ok",
              "data": {
                "results": [
                  {
                    "id": "string — track ID in the source system (Deezer track ID or iTunes trackId)",
                    "title": "string — track title",
                    "artist": "string — artist name",
                    "album": "string | null — album name",
                    "duration_seconds": "number | null — track duration in seconds",
                    "cover_url_small": "string | null — ~50-60px thumbnail",
                    "cover_url_medium": "string | null — ~120-250px thumbnail",
                    "cover_url_big": "string | null — ~500-1000px thumbnail",
                    "preview_url": "string | null — 30-second preview URL (Deezer only, usually null for iTunes)",
                    "release_date": "string | null — ISO 8601 date (YYYY-MM-DD)",
                    "isrc": "string | null — International Standard Recording Code (always null in current version — not available in search responses)",
                    "is_explicit": "boolean — whether track contains explicit content (SDK is responsible for filtering if needed)",
                    "source": "string enum: 'deezer' | 'itunes' — metadata source",
                    "source_url": "string | null — link to track in the source (Deezer/iTunes page)",
                    "yt_query": "string — ready-to-use query for YouTube search, default format: '{artist} - {title} official audio'. SDK may override this when calling /search/youtube."
                  }
                ],
                "total_results": "integer — total number of results available on the server (may exceed len(results))",
                "next_cursor": "string | null — opaque cursor for next page. null means no more results.",
                "query": "string — echo of the original query"
              }
            },
            "example": {
              "status": "ok",
              "data": {
                "results": [
                  {
                    "id": "900552722",
                    "title": "Blinding Lights",
                    "artist": "The Weeknd",
                    "album": "After Hours",
                    "duration_seconds": 200.0,
                    "cover_url_small": "https://e-cdns-images.dzcdn.net/images/cover/.../56x56-000000-80-0-0.jpg",
                    "cover_url_medium": "https://e-cdns-images.dzcdn.net/images/cover/.../250x250-000000-80-0-0.jpg",
                    "cover_url_big": "https://e-cdns-images.dzcdn.net/images/cover/.../500x500-000000-80-0-0.jpg",
                    "preview_url": "https://cdns-preview-c.dzcdn.net/stream/....mp3",
                    "release_date": "2020-03-20",
                    "isrc": null,
                    "is_explicit": false,
                    "source": "deezer",
                    "source_url": "https://www.deezer.com/track/900552722",
                    "yt_query": "The Weeknd - Blinding Lights official audio"
                  }
                ],
                "total_results": 300,
                "next_cursor": "eyJvIjoxMH0=",
                "query": "The Weeknd Blinding Lights"
              }
            }
          },
          "401": {
            "description": "Authentication failed",
            "codes": ["unauthorized"]
          },
          "403": {
            "description": "Plan access denied or insufficient usage tokens",
            "codes": ["subscription_required", "access_forbidden", "insufficient_usage_tokens"]
          },
          "422": {
            "description": "Validation error",
            "codes": ["validation_error"],
            "example": {
              "status": "error",
              "error": {
                "code": "validation_error",
                "detail": "query: String should have at least 1 character",
                "extra": {
                  "errors": [
                    {"loc": ["body", "query"], "msg": "String should have at least 1 character", "type": "string_too_short"}
                  ]
                }
              }
            }
          },
          "429": {
            "description": "Rate limit exceeded",
            "codes": ["rate_limit_exceeded"]
          },
          "502": {
            "description": "All music providers (Deezer and iTunes) failed",
            "codes": ["music_provider_failed"],
            "example": {
              "status": "error",
              "error": {
                "code": "music_provider_failed",
                "detail": "Deezer rate limit exceeded"
              }
            }
          }
        },
        "sdk_notes": [
          "Empty results (0 tracks) is a successful response (200), not an error.",
          "Use 'yt_query' field as-is when calling /search/youtube, OR override it with your own query if you want different search semantics.",
          "'preview_url' is usually null for iTunes results — only Deezer provides previews.",
          "Pagination: pass the opaque 'next_cursor' value back as 'cursor' in the next request. If next_cursor is null, there are no more pages.",
          "'source' field tells which provider returned the data — can be useful for debugging but should not affect business logic."
        ]
      },
      {
        "id": "search_youtube",
        "method": "POST",
        "path": "/search/youtube",
        "endpoint_code": "search.youtube",
        "summary": "Search YouTube videos",
        "description": "Searches YouTube via yt-dlp flat-playlist mode. Accepts any free-form query — not limited to music. The SDK is responsible for building music-specific queries (e.g., using 'yt_query' from /search/music results). Returns video metadata without downloading. Resulting URLs can be passed directly to /downloader/links or /downloader/ for actual download.",
        "plan_access": ["lite", "pro"],
        "token_cost": 2,
        "rate_limit": {
          "hard_cap_per_minute": 15,
          "effective_formula": "min(plan_limit, 15)",
          "note": "Lower cap than /search/music because YouTube search spawns a yt-dlp subprocess (CPU-intensive)."
        },
        "request": {
          "content_type": "application/json",
          "body_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
              "query": {
                "type": "string",
                "min_length": 1,
                "max_length": 300,
                "description": "Free-form search query. For music search, use the 'yt_query' field from /search/music results."
              },
              "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "default": 5,
                "description": "Maximum number of results to return"
              },
              "cursor": {
                "type": "string",
                "nullable": true,
                "default": null,
                "description": "Reserved for future use. In v1, pagination is NOT supported — next_cursor is always null regardless of limit."
              }
            }
          },
          "example": {
            "query": "The Weeknd - Blinding Lights official audio",
            "limit": 5
          }
        },
        "responses": {
          "200": {
            "description": "Search completed successfully",
            "schema": {
              "status": "ok",
              "data": {
                "results": [
                  {
                    "url": "string — full YouTube URL (https://www.youtube.com/watch?v=...)",
                    "title": "string — video title",
                    "channel": "string | null — channel name",
                    "duration_seconds": "number | null — video duration in seconds",
                    "view_count": "integer | null — total view count",
                    "thumbnail_url": "string | null — best available thumbnail URL",
                    "upload_date": "string | null — upload date in YYYYMMDD format"
                  }
                ],
                "next_cursor": "always null in v1",
                "query": "string — echo of the original query"
              }
            },
            "example": {
              "status": "ok",
              "data": {
                "results": [
                  {
                    "url": "https://www.youtube.com/watch?v=4NRXx6U8ABQ",
                    "title": "The Weeknd - Blinding Lights (Official Audio)",
                    "channel": "The Weeknd",
                    "duration_seconds": 200.0,
                    "view_count": 523456789,
                    "thumbnail_url": "https://i.ytimg.com/vi/4NRXx6U8ABQ/maxresdefault.jpg",
                    "upload_date": "20191129"
                  }
                ],
                "next_cursor": null,
                "query": "The Weeknd - Blinding Lights official audio"
              }
            }
          },
          "401": {"description": "Authentication failed", "codes": ["unauthorized"]},
          "403": {"description": "Plan access denied or insufficient usage tokens", "codes": ["subscription_required", "access_forbidden", "insufficient_usage_tokens"]},
          "422": {"description": "Validation error", "codes": ["validation_error"]},
          "429": {"description": "Rate limit exceeded", "codes": ["rate_limit_exceeded"]},
          "502": {
            "description": "YouTube search failed (yt-dlp error, timeout, etc.)",
            "codes": ["youtube_search_failed"],
            "example": {
              "status": "error",
              "error": {
                "code": "youtube_search_failed",
                "detail": "YouTube search timed out after 30s"
              }
            }
          }
        },
        "sdk_notes": [
          "This endpoint is INTENTIONALLY generic — it accepts any text query, not just music. For music-specific search, the SDK should build a music query (e.g., '{artist} - {title} official audio') and pass it here.",
          "To download the found video: take 'url' from results and pass it to POST /downloader/links (returns remote CDN URL) or POST /downloader/ (downloads to server).",
          "For audio-only download, pass mode='audio' to /downloader/links or /downloader/.",
          "Pagination is NOT supported in v1 — 'next_cursor' is always null. If you need more than 25 results, you must change the query.",
          "Empty results (0 videos) is a successful response (200), not an error.",
          "URLs are always in the format 'https://www.youtube.com/watch?v=<ID>' — no shortened youtu.be links."
        ]
      },
      {
        "id": "search_chart",
        "method": "GET",
        "path": "/search/chart",
        "endpoint_code": "search.chart",
        "summary": "Get Deezer global music chart",
        "description": "Returns the Deezer global music chart. Each track includes a pre-built 'yt_query' field for subsequent YouTube search. No iTunes fallback — iTunes does not provide a public chart API.",
        "plan_access": ["free", "lite", "pro"],
        "token_cost": 1,
        "rate_limit": {
          "hard_cap_per_minute": 60,
          "effective_formula": "min(plan_limit, 60)",
          "note": "Shares the same rate limit bucket as /search/music is NOT true — each endpoint has its own bucket. Hard cap is identical though."
        },
        "request": {
          "content_type": "none (GET)",
          "query_parameters": {
            "limit": {
              "type": "integer",
              "minimum": 1,
              "maximum": 25,
              "default": 10,
              "description": "Maximum number of tracks to return"
            },
            "cursor": {
              "type": "string",
              "nullable": true,
              "default": null,
              "description": "Opaque cursor from previous response's 'next_cursor' for pagination"
            }
          },
          "examples": [
            "/search/chart",
            "/search/chart?limit=10",
            "/search/chart?limit=25&cursor=eyJvIjoxMH0="
          ]
        },
        "responses": {
          "200": {
            "description": "Chart retrieved successfully",
            "schema": {
              "status": "ok",
              "data": {
                "results": "array of MusicSearchResult (same shape as /search/music results)",
                "total_results": "integer — total chart size on Deezer",
                "next_cursor": "string | null — opaque cursor for next page"
              }
            },
            "example": {
              "status": "ok",
              "data": {
                "results": [
                  {
                    "id": "2817078962",
                    "title": "Example Hit Track",
                    "artist": "Popular Artist",
                    "album": "Hit Album",
                    "duration_seconds": 180.0,
                    "cover_url_small": "https://e-cdns-images.dzcdn.net/images/cover/.../56x56.jpg",
                    "cover_url_medium": "https://e-cdns-images.dzcdn.net/images/cover/.../250x250.jpg",
                    "cover_url_big": "https://e-cdns-images.dzcdn.net/images/cover/.../500x500.jpg",
                    "preview_url": "https://cdns-preview-x.dzcdn.net/stream/....mp3",
                    "release_date": "2024-01-15",
                    "isrc": null,
                    "is_explicit": false,
                    "source": "deezer",
                    "source_url": "https://www.deezer.com/track/2817078962",
                    "yt_query": "Popular Artist - Example Hit Track official audio"
                  }
                ],
                "total_results": 100,
                "next_cursor": "eyJvIjoxMH0=",
                "country": "us"
              }
            }
          },
          "401": {"description": "Authentication failed", "codes": ["unauthorized"]},
          "403": {"description": "Plan access denied or insufficient usage tokens", "codes": ["subscription_required", "access_forbidden", "insufficient_usage_tokens"]},
          "422": {"description": "Validation error (e.g., invalid limit value)", "codes": ["validation_error"]},
          "429": {"description": "Rate limit exceeded", "codes": ["rate_limit_exceeded"]},
          "502": {
            "description": "Deezer chart unavailable",
            "codes": ["chart_unavailable"],
            "example": {
              "status": "error",
              "error": {
                "code": "chart_unavailable",
                "detail": "Deezer server error: HTTP 503"
              }
            }
          }
        },
        "sdk_notes": [
          "This is a GET endpoint — parameters are in query string, not body.",
          "country='0' is the global chart (default). For country-specific charts, use ISO 3166-1 alpha-2 codes.",
          "Common country codes: 'us' (USA), 'gb' (UK), 'de' (Germany), 'fr' (France), 'ru' (Russia), 'br' (Brazil), 'jp' (Japan), 'mx' (Mexico), 'es' (Spain), 'it' (Italy).",
          "All tracks in results have the same structure as /search/music results — 'source' is always 'deezer' for chart data.",
          "No fallback — if Deezer is down, you get 502. SDK should treat this as a transient error (retry with backoff).",
          "'yt_query' is pre-built and ready to pass to /search/youtube.",
          "Pagination works the same as /search/music — pass next_cursor back as cursor."
        ]
      }
    ],
    "typical_sdk_workflows": [
      {
        "name": "Download a specific track by name",
        "steps": [
          "1. POST /search/music { query: 'Artist Name - Track Title', limit: 5 } → get MusicSearchResult[]",
          "2. Display results to user with cover_url_medium, title, artist, duration_seconds, is_explicit",
          "3. User selects a track → take selected_result.yt_query",
          "4. POST /search/youtube { query: selected_result.yt_query, limit: 5 } → get YoutubeSearchHit[]",
          "5. Display YouTube hits with title, channel, duration_seconds, thumbnail_url (user may want to pick different version)",
          "6. User selects a video → take selected_hit.url",
          "7. POST /downloader/links { url: selected_hit.url, mode: 'audio' } → get download URL (fast-link, no server storage) OR POST /downloader/ { url: selected_hit.url, mode: 'audio', quality: 'max' } → wait for download, get /media/<file>.mp3 URL",
          "8. Optionally: embed ID3 tags on client side using metadata from step 2 (artist, title, album, cover_url_big)"
        ]
      },
      {
        "name": "Browse current music chart and download",
        "steps": [
          "1. GET /search/chart?country=us&limit=25 → get top 25 US chart tracks",
          "2. Display chart to user with cover and metadata",
          "3. Same as workflow #1 from step 3 onwards"
        ]
      },
      {
        "name": "Paginate through search results",
        "steps": [
          "1. POST /search/music { query: 'rock', limit: 25 } → response.data.next_cursor = 'eyJvIjoyNX0='",
          "2. POST /search/music { query: 'rock', limit: 25, cursor: 'eyJvIjoyNX0=' } → next page",
          "3. Continue until next_cursor is null"
        ]
      },
      {
        "name": "YouTube search for non-music content",
        "steps": [
          "1. POST /search/youtube { query: 'how to tie a tie tutorial', limit: 10 } → generic YouTube search",
          "2. Pick url → pass to /downloader/links for video download",
          "Note: /search/youtube is NOT music-specific. The SDK can expose it as a generic YouTube search method."
        ]
      }
    ],
    "error_handling_guidelines": [
      {
        "status": 401,
        "action": "Prompt user to re-authenticate. Token is invalid or revoked."
      },
      {
        "status": 403,
        "codes": ["subscription_required", "access_forbidden"],
        "action": "Inform user that their plan doesn't support this endpoint. Suggest upgrade."
      },
      {
        "status": 403,
        "codes": ["insufficient_usage_tokens"],
        "action": "Inform user they're out of usage tokens. Show current balance and renewal date."
      },
      {
        "status": 422,
        "action": "Developer error — fix request payload. In production, log and show generic error to user."
      },
      {
        "status": 429,
        "action": "Honor Retry-After header. Implement exponential backoff. SDK may queue requests internally."
      },
      {
        "status": 502,
        "codes": ["music_provider_failed", "chart_unavailable", "youtube_search_failed"],
        "action": "Transient error — retry with exponential backoff (max 3 attempts). If persistent, show 'Service temporarily unavailable' to user."
      }
    ],
    "data_type_notes": {
      "MusicSearchResult": "Universal track representation used by /search/music and /search/chart. 'source' field indicates origin (deezer | itunes).",
      "YoutubeSearchHit": "YouTube video representation used by /search/youtube. Does NOT include MusicSearchResult fields — it's a separate structure.",
      "cursor_format": "Opaque base64-encoded JSON. SDK MUST NOT parse or construct cursors manually — only pass through from previous responses.",
      "null_handling": "Many fields can be null (album, duration, covers, etc.). SDK should gracefully handle missing data in UI.",
      "duration_units": "ALWAYS seconds (number, can be fractional). Not milliseconds.",
      "date_formats": {
        "release_date": "ISO 8601 date: YYYY-MM-DD",
        "upload_date": "YouTube format: YYYYMMDD (no separators)"
      }
    },
    "versioning_and_compatibility": {
      "current_version": "1.0.0",
      "backward_compatibility_promise": [
        "Field additions to responses are non-breaking (SDK must ignore unknown fields).",
        "New endpoints under /search/* may be added without notice.",
        "Removal or type changes of existing fields require a major version bump.",
        "Cursor format is opaque — internal structure may change without notice. SDK must never parse cursors."
      ],
      "future_endpoints_roadmap": [
        "POST /search/lyrics — get lyrics for a track (LRCLIB)",
        "POST /search/recognize — Shazam-like audio recognition",
        "GET /search/chart with more parameters (genre, time period)"
      ]
    }
  }
}