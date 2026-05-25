import asyncio
import json
import sys
import os

async def main():
    rsd_host = sys.argv[1]
    rsd_port = int(sys.argv[2])

    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

    rsd = RemoteServiceDiscoveryService((rsd_host, rsd_port))
    await rsd.connect()

    async with DvtProvider(rsd) as dvt, LocationSimulation(dvt) as loc:
        sys.stdout.write("ready\n")
        sys.stdout.flush()

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.decode().strip()
            if not line:
                continue
            try:
                parts = line.split(",")
                lat = float(parts[0])
                lng = float(parts[1])
                await loc.set(lat, lng)
                sys.stdout.write("ok\n")
                sys.stdout.flush()
            except Exception as e:
                sys.stdout.write(f"err:{e}\n")
                sys.stdout.flush()

if __name__ == "__main__":
    asyncio.run(main())
