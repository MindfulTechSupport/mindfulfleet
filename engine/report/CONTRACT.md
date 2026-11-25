# Report contract v1

Every field a device may send. The hub keeps only these and checks each one; this file is
written from engine/report/contract.py (a test keeps them equal). A change that breaks this
list is a new major version, announced to every project that uses the engine.

| Field | Kind | Limits | From | Meaning |
| --- | --- | --- | --- | --- |
| id | id | see kinds | core | The device's own random ID |
| hostname | hostname | see kinds | core | The device's network name |
| ip | address | see kinds | core | The device's address on its network (a flavor may leave it out) |
| model | short | up to 80 characters | core | Board or computer model |
| reporter_version | short | up to 20 characters | core | Version of the reporting program |
| temp_c | number | -50 to 200 | core | Processor temperature, degrees C |
| throttled | number | 0 to 4294967296 | core | Slow-down and low-voltage flags |
| fan_rpm | number | 0 to 100000 | core | Fan speed |
| fan_percent | number | 0 to 100 | core | Fan power, percent |
| input_volts | number | 0 to 60 | core | Power supply volts |
| board_watts | number | 0 to 1000 | core | Board power use |
| cpu_per_core | numbers | 0 to 100, up to 256 values | core | Busy percent per core |
| mem_total | number | 0 to 1125899906842624 | core | Memory size, bytes |
| mem_used | number | 0 to 1125899906842624 | core | Memory in use, bytes |
| disk_total | number | 0 to 1125899906842624 | core | Main disk size, bytes |
| disk_used | number | 0 to 1125899906842624 | core | Main disk used, bytes |
| uptime_s | number | 0 to 10000000000 | core | Seconds since start |
| board_ram_mb | number | 0 to 4194304 | core | Memory fitted on the board, MB |
| cpu_mhz | numbers | 0 to 100000, up to 256 values | core | Speed per core, MHz |
| cpu_max_mhz | number | 0 to 100000 | core | Top speed, MHz |
| load_avg | numbers | 0 to 100000, up to 3 values | core | 1, 5 and 15 minute load |
| swap_total | number | 0 to 1125899906842624 | core | Swap size, bytes |
| swap_used | number | 0 to 1125899906842624 | core | Swap in use, bytes |
| net_rx_bps | number | 0 to 1125899906842624 | core | Network in, bytes a second |
| net_tx_bps | number | 0 to 1125899906842624 | core | Network out, bytes a second |
| core_volts | number | 0 to 10 | core | Processor core volts |
| os_name | text | up to 60 characters | core | Operating system name |
| wifi_dbm | number | -150 to 50 | core | Wi-Fi signal, dBm |
| disk_read_bps | number | 0 to 1125899906842624 | core | Disk reads, bytes a second |
| disk_write_bps | number | 0 to 1125899906842624 | core | Disk writes, bytes a second |
| top_process | text | up to 40 characters | core | Busiest program's name |
| top_process_cpu | number | 0 to 100000 | core | Busiest program's share, percent |
| sensors | sensors | up to 8 | core | Extra power sensors |
| hashrate | number | 0 to 1000000000000.0 | xmrig | Mining speed, hashes a second |
| hashrate_15m | number | 0 to 1000000000000.0 | xmrig | Mining speed, 15-minute average |
| mining_threads | number | 0 to 4096 | xmrig | Mining threads |
| shares_good | number | 0 to 1000000000000000.0 | xmrig | Accepted shares |
| shares_total | number | 0 to 1000000000000000.0 | xmrig | All shares sent |
| pool | text | up to 80 characters | xmrig | Mining pool address |
| pool_tls | text | up to 20 characters | xmrig | Pool link encryption, or none |
| rig_id | text | up to 40 characters | xmrig | Miner's name at the pool |
| algo | text | up to 30 characters | xmrig | Mining algorithm |
| miner_version | text | up to 20 characters | xmrig | Miner program version |
| kernel | text | up to 40 characters | core | Kernel version |
| page_kb | count | whole number 0 to 1048576 | core | Memory page size, KB |
| huge_pages | count | whole number 0 to 1073741824 | core | Huge pages set aside |
| thp | choice | always, madvise, never | core | Transparent huge pages setting |
| ups | ups | see kinds | ups-nut | UPS status, battery charge, minutes left and load |
