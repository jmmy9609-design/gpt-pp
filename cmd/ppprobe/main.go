package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"regexp"
	"time"

	"pp-longlink/internal/gateway"
)

var jwtFindRE = regexp.MustCompile(`eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`)

func main() {
	tokenFile := flag.String("token-file", "", "file containing JWT tokens")
	proxy := flag.String("proxy", "", "proxy")
	limit := flag.Int("limit", 1, "max tokens to test")
	offset := flag.Int("offset", 0, "skip tokens before testing")
	timeout := flag.Duration("timeout", 90*time.Second, "per token timeout")
	httpTimeout := flag.Duration("http-timeout", 15*time.Second, "per upstream HTTP operation timeout")
	maxAttempts := flag.Int("attempts", 1, "racing attempts per proxy candidate")
	raceParallel := flag.Int("parallel", 1, "racing parallelism per token")
	proxyRotations := flag.Int("proxy-rotations", 1, "derived proxy IP rotations per proxy")
	privateOut := flag.String("private-out", "", "write private hosted/paypal URLs for local debugging")
	country := flag.String("country", "DE", "checkout country")
	currency := flag.String("currency", "EUR", "checkout currency")
	flag.Parse()
	if *tokenFile == "" || *proxy == "" {
		fmt.Fprintln(os.Stderr, "missing -token-file or -proxy")
		os.Exit(2)
	}
	raw, err := os.ReadFile(*tokenFile)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	tokens := jwtFindRE.FindAllString(string(raw), -1)
	if *offset > 0 {
		if *offset >= len(tokens) {
			tokens = nil
		} else {
			tokens = tokens[*offset:]
		}
	}
	if *limit > 0 && len(tokens) > *limit {
		tokens = tokens[:*limit]
	}
	cfg := gateway.DefaultConfig()
	cfg.Country = *country
	cfg.Currency = *currency
	cfg.Timeout = *httpTimeout
	cfg.MaxAttempts = *maxAttempts
	cfg.RaceParallel = *raceParallel
	cfg.ProxyRotations = *proxyRotations
	extractor := gateway.NewExtractor(cfg)
	allOK := true
	for idx, token := range tokens {
		ctx, cancel := context.WithTimeout(context.Background(), *timeout)
		start := time.Now()
		result, err := extractor.Extract(ctx, token, *proxy)
		cancel()
		row := map[string]any{
			"index":      idx + 1,
			"ok":         false,
			"elapsed_ms": time.Since(start).Milliseconds(),
		}
		if result != nil {
			row["ok"] = result.OK
			row["code"] = result.Code
			row["zero_verified"] = result.ZeroVerified
			row["amount_display"] = result.AmountDisplay
			row["proxy_scheme"] = result.ProxyScheme
			row["paypal_url_present"] = result.PayPalAuthorizeURL != ""
			row["message"] = result.Message
			if *privateOut != "" {
				_ = appendPrivateResult(*privateOut, idx+1, result)
			}
		}
		if err != nil {
			row["error"] = err.Error()
			allOK = false
		}
		fmt.Println(mustJSON(row))
	}
	if len(tokens) == 0 || !allOK {
		os.Exit(1)
	}
}

func mustJSON(v any) string {
	raw, _ := json.Marshal(v)
	return string(raw)
}

func appendPrivateResult(path string, index int, result *gateway.ExtractResult) error {
	if result == nil {
		return nil
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	row := map[string]any{
		"index":                index,
		"ok":                   result.OK,
		"code":                 result.Code,
		"hosted_checkout_url":  result.HostedCheckoutURL,
		"paypal_authorize_url": result.PayPalAuthorizeURL,
		"amount_display":       result.AmountDisplay,
		"proxy_scheme":         result.ProxyScheme,
	}
	enc := json.NewEncoder(f)
	return enc.Encode(row)
}
