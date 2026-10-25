idf_component_register(
    SRCS
        "app_main.c"
        "app_cfg.c"
        "wifi_net.c"
        "fortune.c"
        "card.c"
        "cli.c"
    PRIV_REQUIRES
        nvs_flash esp_wifi esp_netif esp_event esp_http_client json mbedtls lwip
        epd render
    INCLUDE_DIRS ".")
