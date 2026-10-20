# Portable lighting controller: calibration and local subnet brief

> Owner's brief, received 20 Oct 2026 (filed by screen-machine-26). It is one of a family of calibration runs; see
> [README.md](README.md) for how it maps onto this repo. The text below is the owner's, unchanged.

**Audience:** application engineer maintaining the existing calibration subsystem.  
**Status:** proposed integration brief; hardware timing and deployment checks remain to be performed.  
**Date:** 20 October 2026.

Extend the existing subsystem with a camera-based timing calibration for the PC → Art-Net → DMX/D-Fi → fixture path. Use a screen-flash reference to remove the common camera measurement delay, optionally correcting for measured screen presentation delay. Separately, configure Windows 11 to host the Ethernet lighting subnet so the same PC can run the DSP/application stack and drive the lighting hardware wherever it is deployed.

Implementation names, tolerances, trial counts and persistence fields below are proposals to map onto the existing codebase. This brief does not assume access to that codebase or require replacing its calibration architecture.

## Chapter 1 — Optical calibration within the existing subsystem

### 1.1 Objective and measurement boundary

Estimate the delay between an application lighting trigger and the corresponding physical light response. The camera closes the measurement loop. The returned value is an estimate of **lighting output latency**, covering every stage downstream of the selected trigger: application scheduling, Art-Net output, the node, DMX/D-Fi transmission and fixture response.

Use the same trigger boundary as the existing compensation logic. If that boundary is a DSP event, inject the test event there and retain the normal downstream processing. Timestamping only the final packet send would measure a narrower path; record that timestamp separately for diagnostics if useful.

The design premise is that consumer camera capture may introduce a substantial, initially unknown delay. Its magnitude is to be measured, not assumed. Screen flashing provides a local reference for estimating and subtracting that common camera contribution. Accept any unmeasured physical panel response as a residual error within the agreed timing budget.

### 1.2 Shared capture setup

Perform both loops in a darkened room using one continuously running camera session:

1. Select the camera, capture format and a fixed region of interest (ROI). Observe a surface illuminated by both the screen and the fixture; direct visibility of the screen is unnecessary if its flash produces a detectable reflection.
2. Warm up capture, then hold exposure, gain, white balance and image-processing settings fixed where supported. Keep the camera, ROI and target surface stationary.
3. Use the same brightness extraction and event detector for both loops. A baseline-relative luminance threshold is a suitable first implementation; retain the brightness trace for inspection.
4. Choose pulse brightness and duration to produce a clear response without clipping, with enough separation for the scene to return to baseline. Make these settings configurable.
5. Interleave repeated screen-reference and fixture trials. Keep the fixture off during screen trials and the screen at a constant dark level during fixture trials.

Use a single monotonic timebase for application events, display events and camera-frame receipt. Windows recommends QueryPerformanceCounter (QPC) for high-resolution interval measurements.[1]

For the initial implementation, timestamp each camera frame as early as practical when it reaches the capture callback, before brightness analysis. Use that timestamp as the observed event time in **both** loops. This intentionally includes the capture path being calibrated. Record frame/device timestamps as additional diagnostics when available; Media Foundation exposes driver timestamps related to QPC.[2] Do not mix callback-receipt timestamps in one loop with device timestamps in the other.

Choose and record the capture acquisition mode. Windows MediaFrameReader distinguishes realtime acquisition, which can discard waiting frames, from buffered acquisition, which preserves their order.[3] Keep analysis off the UI thread, drain capture continuously and record any detected gaps or backlog. The cancellation assumption is a sufficiently stable camera pipeline across nearby trials; it does not remove arbitrary changes in buffering between them.

### 1.3 Loop 1 — Screen reference

For each screen trial:

1. Record `t_screen_request` at the defined application instruction boundary.
2. Present a marked white flash using a native display path, preferably a DXGI flip-model swap chain, then return to the dark baseline.
3. Associate that marked frame with its presentation/refresh timing and record `t_screen_present` when reliably obtainable.
4. Detect the optical transition in the camera ROI and record `t_screen_observed` using the shared frame-receipt timestamp convention.

Derive, for trial `i`:

```text
screen_loop[i] = t_screen_observed[i] - t_screen_request[i]
screen_present_delay[i] = t_screen_present[i] - t_screen_request[i]
camera_reference[i] = screen_loop[i] - screen_present_delay[i]
```

The camera reference includes any physical panel response not accounted for by the presentation timestamp. That is the accepted residual term, rather than a reason to block implementation.

DXGI frame statistics provide presentation counts, refresh counts and QPC-related timing.[4] Correlate those records with the actual flash frame; do not interpret an arbitrary `SyncQPCTime` value or the return from a presentation call as proof that the flash was displayed. Frame statistics have availability constraints, including the swap-chain presentation model.[4]

If trustworthy presentation timing is unavailable, retain the screen-loop measurement and return the relative estimate described below. Mark the correction as unavailable instead of silently claiming a measured zero display delay. No fixed millisecond assumption about the display or camera is required.

### 1.4 Loop 2 — Full lighting path

For each fixture trial:

1. Record `t_lighting_trigger` at the agreed calibration boundary.
2. Send a brightness pulse through the normal application/DSP, Art-Net and D-Fi output path, using the production interface, universe, fixture mode and scheduling settings.
3. Detect the fixture's optical response in the same camera ROI and record `t_lighting_observed`.
4. Record missing responses, timeouts and ambiguous detections as failed trials; do not convert them to zero latency.

```text
lighting_loop[j] = t_lighting_observed[j] - t_lighting_trigger[j]
```

Keep the regular output engine active. The test pulse should travel through the same output cadence and queues as a normal lighting command. Art-Net uses UDP port 6454; it is the optical observation that closes this calibration loop.[5]

### 1.5 Estimate and apply the correction

Use a robust central estimate across accepted trials, initially the median:

```text
estimated_camera_reference = median(camera_reference[])
estimated_lighting_output = median(lighting_loop[]) - estimated_camera_reference
```

Equivalently, the conceptual relationship is:

```text
lighting output estimate
    = lighting-loop measurement
    - screen-loop measurement
    + screen presentation delay
```

When display timing is unavailable:

```text
lighting_relative_to_screen = median(lighting_loop[]) - median(screen_loop[])
```

Store which estimate was produced and the timing boundaries used. The relative result retains the screen's output delay as a bias. The presentation-corrected result retains unmeasured panel response and detection/sampling uncertainty. These are engineering estimates for compensation, with no claim of isolating Art-Net transport alone.

Report the raw screen and lighting-loop distributions, accepted/failed trial counts, corrected estimate and an uncertainty/status indicator. Camera frame sampling limits the timing evidence available from each observation; repeated trials should expose that variability rather than produce unsupported precision. Do not label raw lighting-loop jitter as fixture-only jitter, since the camera also contributes.

Pass an accepted result into the existing timing-compensation mechanism. Preserve its established policy for scheduling, lookahead or delaying another signal. Keep the last valid profile when a run fails; do not overwrite it with an invalid, negative or insufficiently supported estimate.

### 1.6 Integration and stored evidence

Add a calibration mode/provider and result type within the existing subsystem, reusing its run lifecycle, cancellation, profile storage and result review. Suggested responsibilities are:

| Responsibility | Proposed integration |
| --- | --- |
| Trial orchestration | Alternate screen and fixture trials; collect timestamps and validation results |
| Optical detector | Extract ROI brightness and identify the event with a consistent rule |
| Display reference | Render the marked flash and correlate presentation telemetry |
| Lighting probe | Inject the pulse at the normal calibration trigger boundary |
| Estimator | Compute the reference, corrected delay, variability and quality status |
| Profile adapter | Persist results and supply the accepted delay to existing compensation |

Persist the run time; clock/timestamp convention; camera identity, format, settings and ROI; display identity and presentation method; Ethernet adapter identity; node identity/address; Art-Net port-address/universe mapping; fixture mode and D-Fi path; raw timestamps/traces; trial rejection reasons; estimates; and correction availability. Map these onto the existing schema rather than introduce a parallel profile store.

Make the timing tolerance, detector settings, minimum accepted trials and quality thresholds explicit configuration. Recalibrate or flag a profile for review after changes to capture configuration, display timing, output scheduling, node/fixture settings or the physical transmission path.

### 1.7 Verification criteria

- A screen-only run produces repeatable detections and correlates them with the intended displayed frame.
- Introducing a known software delay downstream of the lighting trigger changes the estimated lighting delay by that amount within the configured tolerance.
- Adding the same controlled capture-processing delay to both loops leaves the corrected lighting estimate approximately unchanged within that tolerance.
- Repeated trials under representative DSP/rendering load report variability and failed detections, rather than hide them through averaging or clamping.
- Cancellation and failure restore the previous application/lighting state and retain the last valid calibration.
- Saved profiles reproduce the timestamp boundary and hardware/configuration evidence used to obtain the result.

## Chapter 2 — Local Ethernet subnet for a portable application stack

### 2.1 Deployment arrangement

Use the Windows 11 PC for compute and the Ethernet lighting network. Its Wi-Fi interface supplies upstream connectivity when needed. Its Ethernet interface supplies the private subnet and connects directly to an Art-Net node, or through a switch for multiple nodes.

Windows Internet Connection Sharing (ICS) supplies DHCP on the private interface and NAT through the upstream interface; a custom DHCP/router server is unnecessary for this configuration.[6][7] The application stack supplies DSP and Art-Net output.

Confirm the hardware chain before implementation. Chauvet's D-Fi Hub accepts DMX through XLR. An Ethernet Art-Net feed therefore needs an Art-Net-to-DMX node before that transmitter unless the selected hardware combines those functions. Chauvet documents DMX-AN2 as an Ethernet Art-Net/sACN-to-DMX interface.[8][9] The actual node/transmitter model is still to be supplied.

### 2.2 Provision Windows networking

Perform provisioning with an administrator account:

1. Connect the PC to its upstream network using Wi-Fi and attach the Ethernet node/switch.
2. Open `ncpa.cpl`, then open **Wi-Fi adapter → Properties → Sharing**.
3. Enable **Allow other network users to connect through this computer's Internet connection** and select the appliance-facing Ethernet adapter where the selection is offered.[10]
4. Read the resulting Ethernet address and mask using Windows adapter information or `ipconfig /all`. Microsoft documents `192.168.137.0/24` for full ICS; discover the deployed configuration rather than hard-code it.[6]
5. Enable DHCP on the node if supported. Otherwise configure a compatible, non-conflicting static address according to its manual. Art-Net supports DHCP and static addressing; support on the particular node must be checked.[5]
6. Permit the application's required Art-Net UDP 6454 traffic through Windows Firewall on the lighting interface/subnet, respecting any local outbound policy as well as required inbound replies/discovery. Scope rules to the application and intended network.[5][11]

Keep the private Ethernet segment separate from networks already running another DHCP server. Do not bridge the Wi-Fi and Ethernet interfaces: the intended design is a distinct local lighting subnet.

Illustrative addresses only:

| Endpoint | Example | Use |
| --- | --- | --- |
| PC Wi-Fi | Assigned by the venue/home network | Upstream connectivity |
| PC Ethernet | `192.168.137.1/24` | Lighting interface, subnet gateway and host for local services |
| Art-Net node | `192.168.137.20/24` | Lighting packet destination; actual address discovered/configured |

### 2.3 Bind the application to Ethernet

Persist the selected adapter's stable identity and resolve its current IPv4 address at startup. Revalidate it after adapter/address changes. Bind Art-Net output and discovery to that Ethernet address; use the node's local IP as the unicast destination where supported. Use correctly scoped subnet discovery when needed. Keep Art-Net port-address settings aligned with the node; the protocol's **Sub-Net** universe grouping is distinct from the IP subnet mask.[5]

The gateway IP is an address of the PC itself. Services running natively on the PC may listen on that Ethernet address and their own ports; they do not each need a DHCP lease. Separate container or VM interfaces require their own networking arrangement. The stack's runtime—native Windows, Docker or WSL—remains an integration detail to confirm; demonstrate Art-Net discovery and output from the actual production runtime.

The packet path to a node on the same Ethernet subnet is local. With the correct interface binding, lighting packets do not traverse upstream Wi-Fi, so upstream UDP restrictions do not govern this path. This is a consequence of the topology, not an exemption from the PC's own firewall rules. Internet-dependent features remain subject to the upstream network.

### 2.4 Portable startup and validation

At startup, show a compact status for the selected Ethernet adapter/address, node reachability/discovery, configured universe and loaded calibration profile. Resolve changed node addresses rather than assume a previous DHCP lease remains current. Validate output with a test cue before enabling compensation from a stored profile.

Test the complete deployment on the target Windows build:

- Run the application/DSP stack and drive the real node/transmitter/fixture through Ethernet.
- Confirm with packet capture that Art-Net uses Ethernet and its intended local destinations.
- Remove upstream internet access and confirm local lighting and calibration continue. Microsoft documents continued private-side DHCP when full ICS's public interface has no internet access.[6]
- Separately test Wi-Fi disconnection, an offline cold boot, reboot persistence, and Ethernet/node unplug/replug. Loss of internet and absence of an upstream interface at startup are different cases; do not assume the former proves the latter.
- If offline startup exposes an ICS limitation on the target system, provide a documented static Ethernet/node profile for local operation with DHCP disabled in that fallback mode. A requirement for automatic DHCP from an offline cold boot must be validated explicitly before claiming it is satisfied.

Acceptance is a PC that runs the stack, hosts the local lighting network, sends Art-Net over Ethernet, and loads a calibration associated with the current hardware/configuration. No venue router configuration should be needed for the local lighting path.

## Documentary references

These sources document platform/protocol behaviour. The trial structure, estimator, profile integration and acceptance checks above are proposed engineering requirements.

1. Microsoft Learn — [Acquiring high-resolution time stamps](https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps).
2. Microsoft Learn — [MFSampleExtension_DeviceTimestamp attribute](https://learn.microsoft.com/en-us/windows/win32/medfound/mfsampleextension-devicetimestamp).
3. Microsoft Learn — [Process media frames with MediaFrameReader](https://learn.microsoft.com/en-us/windows/apps/develop/camera/process-media-frames-with-mediaframereader).
4. Microsoft Learn — [DXGI_FRAME_STATISTICS structure](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_frame_statistics).
5. Artistic Licence — [Art-Net 4 protocol specification](https://art-net.org.uk/downloads/art-net.pdf), especially IP configuration, UDP transport and port-address terminology.
6. Microsoft Learn — [Use Wireless Hosted Network, Internet Connection Sharing](https://learn.microsoft.com/en-us/windows/win32/nativewifi/using-hosted-network-and-internet-connection-sharing), particularly its full ICS/private-interface description. This is legacy API documentation; validate the target Windows 11 deployment behaviour.
7. Microsoft Learn — [Wi-Fi Policy CSP: AllowInternetSharing](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-wifi#allowinternetsharing).
8. CHAUVET DJ — [D-Fi Hub specifications](https://www.chauvetdj.com/products/d-fi-hub/).
9. CHAUVET DJ — [DMX-AN2 specifications](https://www.chauvetdj.com/products/dmx-an2/).
10. Listen Technologies — [Sharing an Internet Connection to Connect to Cloud Services, LTN0050](https://www.listentech.com/wp-content/uploads/2021/01/LTN0050-Sharing-a-Wireless-Internet-Connection-to-Connect-to-Cloud-Services-22-01-28-V2.pdf). Documents the Wi-Fi-to-Ethernet Sharing dialog workflow for Windows 8/10; verify UI availability on the target Windows 11 PC.
11. Microsoft Learn — [Configure Windows Firewall rules](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/configure).
