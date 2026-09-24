# hyprmasq

Small Hyprland plugin for compositor-side window screenshots.

V1 exposes one hyprctl command:

```sh
hyprctl hyprmasq screenshot 0xWINDOW_ADDRESS /tmp/window.png
```

`0xWINDOW_ADDRESS` is the `address` field from `hyprctl -j clients`.
The output format is PNG.
