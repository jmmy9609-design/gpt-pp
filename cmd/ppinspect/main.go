package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"pp-longlink/internal/gateway"
)

func main() {
	token, _ := os.ReadFile("/tmp/pp_at.txt")
	proxy, _ := os.ReadFile("/tmp/pp_proxy_url.txt")
	cfg := gateway.DefaultConfig()
	cfg.Country = "US"
	cfg.Currency = "USD"
	cfg.Timeout = 25 * time.Second
	res, err := gateway.NewExtractor(cfg).Extract(context.Background(), string(token), string(proxy))
	raw, _ := json.MarshalIndent(map[string]any{"result": res, "error": fmt.Sprint(err)}, "", "  ")
	fmt.Println(string(raw))
	if err != nil {
		os.Exit(1)
	}
}
