#pragma once

#include <cstddef>
#include <optional>
#include <string>

class CFunctionHook;

// Several hooks of one Hyprland function, of several plugins, on Hyprland 0.56 and 0.57, whose CFunctionHook::hook()
// refuses a function that is hooked already.
//
// This file and HookChain.cpp are the same in every plugin that takes part, and depend on nothing but Hyprland's
// headers. The plugins never call each other: all that the hooks of a function share lies in Hyprland's hook system and
// in the code that it wrote, which each plugin reads and writes with its own copy of this code. So a plugin loads,
// unloads, installs and removes its hooks without knowing of the others, in any order. Main thread only, as Hyprland's
// hooks are.
//
// The layout, protocol 1. Both files of a plugin change together with those of the others when it changes.
//
// - The first hook of a function goes in through CFunctionHook::hook(). That replaces the function's first
//   instructions with a jump to the slot, the launch trampoline of that hook, `movabs rax, destination; jmp rax`, and
//   copies those instructions, with a jump to the rest of the function, to the end, the land trampoline of that hook.
//   Hyprland never frees or reuses either, so both stay where they are while any hook of the function is in, also
//   after the first hook has gone.
// - A later hook joins: it does not change the function. It gets a thunk of the same code as the slot, in memory of its
//   plugin that stays readable, writable and executable, which jumps to the destination that the slot named, and the
//   slot then names the destination of the new hook. So a call runs through the newest hook first, which calls the
//   older ones through its m_original, down to the function itself.
// - Every hook of the function has in its CFunctionHook what hook() leaves there: the slot as its launch trampoline,
//   the end as its land trampoline, the function's first bytes and their length, and m_active. Its m_original is the
//   end for a hook that went in through hook(), and its thunk for one that joined. A plugin writes only to the
//   CFunctionHooks of its own, and for others only the address in the slot or in a thunk.
// - A hook leaves by giving whatever names its destination, the slot or the thunk of the hook before it, what its own
//   m_original led to. The last hook to leave puts the function's first bytes back, as CFunctionHook::unhook() does.
//
// A hook of a plugin that does not know this joins and is joined like any other, as it went in through hook(). When
// such a plugin removes its hook, or Hyprland removes the hooks of a plugin that it unloads by force, unhook() puts the
// function's first bytes back, which are the right ones in every hook: the function then runs unhooked, and no hook of
// it is called until its plugin installs it again. linked() tells. The hooks that were dropped still name each other,
// and what one of them went on to may be code of a plugin that has left since. So a plugin calls a hook's m_original
// only from that hook's code, in a call that reached it through the function, and asks next() anywhere else. Then
// nothing jumps into a plugin that is gone.
//
// A call of a hooked function that a plugin makes itself goes to the function, where every hook of it sees the call,
// and the plugin's own hook passes it on. Through its hook's m_original the call would skip the hooks that joined
// later, which then miss it or not by who went in first.
namespace hookchain {
    inline constexpr int PROTOCOL = 1;

    // Whether a plugin's hook of the function at source is in.
    bool hooked(void* source);
    // Why a hook cannot join those of the function at source, which is hooked: the code there is not what Hyprland's
    // hook system writes, or one of its hooks is unknown to the hook system. Nothing changes.
    std::optional<std::string> refusal(void* source);
    // Installs a hook that HyprlandAPI::createFunctionHook made, in front of the hooks of its function, which is hooked,
    // or tells why not, see refusal(). The hook's m_original is then the next hook, or the function.
    std::optional<std::string> join(CFunctionHook& hook);

    enum class eLeft {
        NOT_INSTALLED, // the hook was not in
        SPLICED,       // the other hooks of the function stay
        LAST,          // it was the function's only hook, and the function is as Hyprland built it
        DROPPED,       // the function's hooks had gone already, through an unhook() that knows nothing of this
        RESET,         // the function's hooks could not be read, so the function is as Hyprland built it, without any
    };
    // Removes a hook, however it went in, and leaves the others. Its plugin then keeps or removes the CFunctionHook,
    // which is not active any more.
    eLeft leave(CFunctionHook& hook);

    // Whether the hook is in and calls of its function reach it.
    bool linked(CFunctionHook& hook);
    // Where a call that the hook's plugin passes on goes: the hook's m_original while calls of the function reach the
    // hook, and the function itself otherwise, when m_original may lead into a plugin that has left.
    void* next(CFunctionHook& hook);
    // The hooks of the function at source that calls reach. 0 for a function that is not hooked, or whose hooks cannot
    // be read.
    size_t members(void* source);

    // Frees the plugin's thunks at its unload, after every hook of the plugin has left.
    void shutdown();
}
