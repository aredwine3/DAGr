In project management (and in DAGr), there are two different ways to look at your schedule. To understand them, imagine you have **Task A (4 hours)** and **Task B (4 hours)**. Both are ready to start immediately at 9:00 AM. 

---

### The Unconstrained Schedule (Mathematical Boundaries)
This is what `calculate_schedule()` does. It asks the question: **"If I had an infinite number of people working for me, how fast could this project finish?"**

In this imaginary world:
*   **Task A** and **Task B** both start at exactly 9:00 AM because you have two people to do them at the same time.

This gives us the mathematical boundaries:
1.  **Earliest Start:** The absolute earliest a task can begin (assuming everything before it finishes as fast as possible).
2.  **Earliest Finish:** The earliest a task can be completed (Earliest Start + Duration).
3.  **Latest Finish:** The absolute latest a task can finish *without delaying the final deadline of the entire project*. 
4.  **Latest Start:** The latest you can begin a task *without delaying the project* (Latest Finish - Duration).
5.  **Slack (or Float):** The difference between the Earliest Start and Latest Start. If a task has 10 hours of slack, you can delay starting it for 10 hours without pushing back the final project completion date! 
6.  **Critical Path:** Any task with **0 slack**. If a critical path task is delayed by even 1 minute, the *entire project* is delayed by 1 minute.

*Note: DAGr's `dagr show` command displays these exact mathematical boundaries.*

---

### The Constrained Schedule (Realistic Single-Person Plan)
This is what `resource_level()` does. It asks the question: **"Since there is only *one* of me, what is the realistic order I should do these things, and when will I actually finish?"**

In the real world:
*   You cannot do Task A and Task B at the same time. You must pick one.
*   DAGr looks at the **slack** of both tasks. It forces you to do the task with the *lowest* slack first (the most "critical" task).
*   If Task A is critical, DAGr schedules Task A from 9:00 AM to 1:00 PM.
*   DAGr then schedules Task B from 1:00 PM to 5:00 PM.

This is your realistic plan. It only has two values:
1.  **Projected Start:** When you will *actually* start the task based on your human capacity.
2.  **Projected Finish:** When you will *actually* finish the task.

*Note: DAGr's `dagr daily` and `dagr status` use this realistic plan.*

---

### Which one predicts if tasks will be LATE?
**Both of them do, but they mean slightly different things:**

1.  If a task is **LATE** in the *Unconstrained* schedule (`calculate_schedule` / `dagr show`), it means: *"Even if I had an infinite number of clones helping me, it is mathematically impossible to meet this deadline."* This is a catastrophic failure of the project plan.
2.  If a task is **LATE** in the *Constrained* schedule (`resource_level` / `dagr status`), it means: *"Because there is only one of me, I will not finish this on time based on my current workload."*

*(DAGr actually checks deadlines against both. `dagr schedule` flags them based on the unconstrained math, while `dagr status` flags them based on your realistic single-person timeline).*