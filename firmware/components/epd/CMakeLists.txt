cmake_minimum_required(VERSION 3.16)
idf_component_register(
    SRCS "epd.c" "uc8251d_minimal.c" "uc8151_minimal.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES driver esp_timer)
