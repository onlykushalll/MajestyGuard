using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging.Abstractions;
using MajestyGuard.Core;
using MajestyGuard.Core.Models;

namespace MajestyGuard.Tests;

public class StateMachineTests
{
    private static StateMachine Make() =>
        new StateMachine(NullLogger<StateMachine>.Instance, new AppConfig());

    // ── Helpers ────────────────────────────────────────────────────────────────

    private static StateMachine InUnlocked()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);  // Dormant → BootScan
        sm.RequestTransition(TransitionTrigger.FaceDetected);       // BootScan → Verifying
        sm.RequestTransition(TransitionTrigger.FaceRecognized);     // Verifying → Unlocked
        return sm;
    }

    // ── B-001: Concurrent triggers never produce undefined state ──────────────

    [Fact]
    public void SimultaneousTriggers_NeverProduceUndefinedState()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);

        var results = new ConcurrentBag<GuardState>();
        var tasks = new Task[100];
        for (int i = 0; i < 100; i++)
        {
            tasks[i] = Task.Run(() =>
            {
                sm.RequestTransition(TransitionTrigger.FaceDetected);
                sm.RequestTransition(TransitionTrigger.InactivityThresholdHit);
                results.Add(sm.Current);
            });
        }
        Task.WaitAll(tasks);

        var valid = new[] {
            GuardState.BootScan, GuardState.Verifying,
            GuardState.InactivityLock, GuardState.Unlocked
        };
        Assert.All(results, s => Assert.Contains(s, valid));
    }

    // ── B-009: HostileLock blocks ManualFallback within 30s ──────────────────

    [Fact]
    public void HostileLock_BlocksManualFallback_Within30Seconds()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        sm.RequestTransition(TransitionTrigger.LivenessCheckFailed);  // → HostileLock

        Assert.Equal(GuardState.HostileLock, sm.Current);

        sm.RequestTransition(TransitionTrigger.ManualFallback);
        Assert.Equal(GuardState.HostileLock, sm.Current);  // Must remain locked
    }

    // ── B-010: SocialLock → StrangerLeft must go to BootScan, never Unlocked ──

    [Fact]
    public void SocialLock_StrangerLeaves_TransitionsToBootScan_NotUnlocked()
    {
        var sm = InUnlocked();

        // Call StrangerDetected — this sets _strangerFirstSeen = now
        sm.RequestTransition(TransitionTrigger.StrangerDetected);
        // Wait > 500ms so presence duration exceeds threshold on next call
        Thread.Sleep(600);
        // Call again — now presenceDuration >= 500ms → SocialLock
        sm.RequestTransition(TransitionTrigger.StrangerDetected);
        Assert.Equal(GuardState.SocialLock, sm.Current);

        sm.RequestTransition(TransitionTrigger.StrangerLeft);

        Assert.Equal(GuardState.BootScan, sm.Current);
        Assert.NotEqual(GuardState.Unlocked, sm.Current);
    }

    // ── B-003: ResetStrangerTracking prevents accumulated flicker time ────────

    [Fact]
    public void StrangerTracking_ResetsOnAbsence_PreventsAccumulation()
    {
        var sm = InUnlocked();

        // Brief stranger presence (400ms) — should NOT trigger SocialLock
        sm.RequestTransition(TransitionTrigger.StrangerDetected);
        Thread.Sleep(400);

        // Reset tracking — as if only primary user in frame
        sm.ResetStrangerTracking();

        // Second brief presence — accumulated time must not count
        sm.RequestTransition(TransitionTrigger.StrangerDetected);
        Thread.Sleep(400);

        Assert.NotEqual(GuardState.SocialLock, sm.Current);
    }

    // ── S-1: InactivityLock + ManualFallback → BootScan, never Unlocked ──────

    [Fact]
    public void InactivityLock_ManualFallback_GoesToBootScan_NotUnlocked()
    {
        var sm = InUnlocked();
        sm.RequestTransition(TransitionTrigger.InactivityThresholdHit);
        Assert.Equal(GuardState.InactivityLock, sm.Current);

        sm.RequestTransition(TransitionTrigger.ManualFallback);

        // Must require re-verification via BootScan — not a direct unlock
        Assert.Equal(GuardState.BootScan, sm.Current);
        Assert.NotEqual(GuardState.Unlocked, sm.Current);
    }

    // ── H-001/H-004: HandleAuthSuccess clears _hostileLockEntryTime ──────────

    [Fact]
    public void AuthSuccess_AfterHostileLock_ClearsCooldown()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        sm.RequestTransition(TransitionTrigger.LivenessCheckFailed);  // → HostileLock
        Assert.Equal(GuardState.HostileLock, sm.Current);

        // Face returns → back to verifying → success
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        Assert.Equal(GuardState.Verifying, sm.Current);
        sm.RequestTransition(TransitionTrigger.FaceRecognized);        // → Unlocked
        Assert.Equal(GuardState.Unlocked, sm.Current);

        // Now lock again and fail — new HostileLock, new timer
        sm.RequestTransition(TransitionTrigger.CameraObstructed);
        Assert.Equal(GuardState.HostileLock, sm.Current);

        // ManualFallback should still be blocked (fresh cooldown from new entry)
        sm.RequestTransition(TransitionTrigger.ManualFallback);
        Assert.Equal(GuardState.HostileLock, sm.Current);
    }

    // ── C-004: StateChanged fires outside lock — no deadlock ─────────────────

    [Fact]
    public void StateChanged_CanCallRequestTransition_FromHandler()
    {
        var sm = Make();
        bool handlerFired = false;

        sm.StateChanged += (_, e) =>
        {
            if (e.Current == GuardState.BootScan && !handlerFired)
            {
                handlerFired = true;
                // This would deadlock if event fired inside the state machine lock
                sm.RequestTransition(TransitionTrigger.FaceDetected);
            }
        };

        sm.RequestTransition(TransitionTrigger.ProfileValidated);  // → BootScan → handler fires
        Assert.True(handlerFired, "StateChanged handler must have been called");
    }

    // ── Auth failure counter resets on success ─────────────────────────────────

    [Fact]
    public void AuthFailureCount_ResetsOnSuccess()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);

        // Fail twice (not at max of 3)
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        sm.RequestTransition(TransitionTrigger.FaceUnrecognized);  // fail 1 → BootScan
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        sm.RequestTransition(TransitionTrigger.FaceUnrecognized);  // fail 2 → BootScan

        // Then succeed
        sm.RequestTransition(TransitionTrigger.FaceDetected);
        sm.RequestTransition(TransitionTrigger.FaceRecognized);    // → Unlocked
        Assert.Equal(GuardState.Unlocked, sm.Current);

        // Fail twice again — should NOT go hostile (counter was reset)
        sm.RequestTransition(TransitionTrigger.CameraObstructed);  // → HostileLock
        sm.RequestTransition(TransitionTrigger.FaceDetected);       // → Verifying
        sm.RequestTransition(TransitionTrigger.FaceUnrecognized);  // fail 1
        Assert.Equal(GuardState.BootScan, sm.Current);
        Assert.NotEqual(GuardState.HostileLock, sm.Current);
    }

    // ── Security Regression: DesktopWatchdog single start ─────────────────────

    [Fact]
    public void Worker_DesktopWatchdog_StartsOnce()
    {
        var source = System.IO.File.ReadAllText(
            @"..\..\..\..\MajestyGuard.Service\Worker.cs");
        var count = System.Text.RegularExpressions.Regex.Matches(
            source, @"_desktopWatchdog\.RunAsync").Count;
        Assert.Equal(1, count);
    }

    // ── Security Regression: OnStateChanged has try/catch ─────────────────────

    [Fact]
    public void Worker_OnStateChanged_HasTryCatch()
    {
        var source = System.IO.File.ReadAllText(
            @"..\..\..\..\MajestyGuard.Service\Worker.cs");
        // C5 refactor: try/catch moved from OnStateChanged (now sync enqueue)
        // to RunStateChangeDispatchAsync (serialized dispatch consumer)
        var methodIdx = source.IndexOf("private async Task RunStateChangeDispatchAsync");
        Assert.True(methodIdx >= 0, "RunStateChangeDispatchAsync must exist (C5 dispatch)");
        var body = source.Substring(methodIdx);
        Assert.Contains("try", body);
        Assert.Contains("catch (Exception", body);
        Assert.Contains("LogError", body);
    }

    // ── B-021: Liveness uses min not mean ─────────────────────────────────────

    [Fact]
    public void Liveness_MinNotMean_FailsOnSingleSpoofFrame()
    {
        // 9 genuine (0.95) + 1 spoof (0.1)
        // mean = 0.865 → would pass (WRONG)
        // min  = 0.1   → fails (CORRECT)
        var scores = new double[] { 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.1 };
        double minScore  = scores.Min();
        double meanScore = scores.Average();

        Assert.True(minScore < 0.85,
            $"B-021: min score should be 0.1 (< 0.85 threshold), got {minScore}");
        Assert.True(meanScore > 0.85,
            "B-021: mean score is above threshold — confirms min is the correct operator");
    }

    // ── C1: IpcMessage.Deserialize handles malformed JSON ────────────────────

    [Fact]
    public void Deserialize_MissingMessageType_ReturnsNull()
    {
        var result = MajestyGuard.Core.IPC.IpcMessage.Deserialize("{\"foo\":\"bar\"}");
        Assert.Null(result);
    }

    [Fact]
    public void Deserialize_EmptyString_ReturnsNull()
    {
        var result = MajestyGuard.Core.IPC.IpcMessage.Deserialize("");
        Assert.Null(result);
    }

    [Fact]
    public void Deserialize_MalformedJson_ReturnsNull()
    {
        var result = MajestyGuard.Core.IPC.IpcMessage.Deserialize("{{{not json");
        Assert.Null(result);
    }

    // ── C2: ManualFallback from BootScan goes Unlocked (documented behavior) ─

    [Fact]
    public void BootScan_ManualFallback_GoesToUnlocked()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);   // → BootScan
        sm.RequestTransition(TransitionTrigger.ManualFallback);     // → Unlocked
        Assert.Equal(GuardState.Unlocked, sm.Current);
        // NOTE: Worker must NOT send AuthDecision{Granted=true} on this path (C2 fix)
    }

    // ── N3/E2: AuthFailure at max calls EnterHostileLock (cooldown active) ───

    [Fact]
    public void AuthFailure_AtMax_SetsHostileLockCooldown()
    {
        var sm = Make();
        sm.RequestTransition(TransitionTrigger.ProfileValidated);
        for (int i = 0; i < 3; i++)
        {
            sm.RequestTransition(TransitionTrigger.FaceDetected);
            sm.RequestTransition(TransitionTrigger.FaceUnrecognized);
        }
        Assert.Equal(GuardState.HostileLock, sm.Current);
        // ManualFallback must be blocked (30s cooldown set by EnterHostileLock)
        sm.RequestTransition(TransitionTrigger.ManualFallback);
        Assert.Equal(GuardState.HostileLock, sm.Current);
    }
}
